from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Sequence
from typing import Any, cast

from ag_ui.core import ActivityMessage, ActivitySnapshotEvent, Message
from asgiref.sync import markcoroutinefunction
from django.core.exceptions import ValidationError
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.response import HttpResponseBase
from django_spec_agents import IdentifierTooLongError
from django_spec_agents.child_sessions.types.child_session import ChildSession
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.types.conversation_meta import ConversationMeta
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.persistence.types.run_store import RunStore
from django_spec_agents.persistence.types.thread_run_state import ThreadRunState
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.fresh_list_response import fresh_list_response
from django_ag_ui.persistence.thread_status_fields import thread_status_fields
from django_ag_ui.persistence.types.child_threads import ChildThreads
from django_ag_ui.persistence.types.mount_identity import MountIdentity
from django_ag_ui.persistence.types.thread_activity import ThreadActivity
from django_ag_ui.persistence.types.thread_activity_source import ThreadActivitySource
from django_ag_ui.persistence.utils import messages_from_jsonable, messages_to_jsonable
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated

# The model stores back ``title`` with ``CharField(max_length=255)``; cap the
# rename here (truncate — a title is cosmetic) so an over-long PATCH is a clean
# save on every backend rather than a ``DataError`` on a strict database.
_MAX_TITLE_LEN = 255


def _valid_pks(model: Any, ids: set[str]) -> list[Any]:
    """The ids ``model``'s pk can hold; another mount's routine id in a shared store is not one."""
    pks = []
    for value in ids:
        try:
            pks.append(model._meta.pk.to_python(value))
        except (ValueError, ValidationError):
            continue
    return pks


class ThreadsView:
    """Owner-scoped thread index endpoint for the chat-history drawer (async, JSON).

    Mounted by [`AGUIServer`][django_ag_ui.AGUIServer] whenever
    ``conversation_store=`` is a live store, over the same
    [`ConversationStore`][django_ag_ui.ConversationStore] the agent view uses:

    - ``GET    <prefix>threads/``       → the user's threads, **metadata only**
      (``{"threads": [...]}``);
    - ``GET    <prefix>threads/<id>/``  → that thread's messages
      (``{"thread_id", "messages"}``);
    - ``PATCH  <prefix>threads/<id>/``  → rename (body ``{"title": "..."}``);
    - ``DELETE <prefix>threads/<id>/``  → delete the thread (``204``).

    Every operation is scoped to the acting user: the store filters by owner, so
    a thread owned by another user simply isn't found (``404``) — never another
    user's history. The view carries the same authentication seam as
    [`DjangoAGUIView`][django_ag_ui.DjangoAGUIView] (``require_authenticated`` /
    ``get_user``, sync or async), and the closed default is load-bearing here:
    every route is owner-scoped, so an anonymous caller has no history to reach.

    A ``thread_activity_source=`` merges pushed activities back into the read
    thread — see
    [`ThreadActivitySource`][django_ag_ui.ThreadActivitySource] for why they are
    not in the stored history to begin with.

    With ``run_store=``, each listed row also carries its run status (``status``,
    ``last_read_at``, ``reason`` when failed, ``interrupts`` when waiting, plus ``run_id`` with
    ``resumable=True``, ``agent_id`` from ``mount``); a ``mount`` with any identity set adds an
    ``agent`` object to the list body. The list answers ``If-None-Match`` with ``304``, and
    ``Accept: text/event-stream`` streams the same body each time it changes, its ETag as the
    frame id, ``Last-Event-ID`` skipping an unchanged first frame. The stream ends after five
    minutes so the client reconnects and is authenticated again, and under WSGI the same request
    answers as a poll.

    Every row carries ``parent``: ``{"thread_id", "scope"}`` for a child session, the
    thread that started it in that thread's own scope, or ``None`` for a thread of its own.
    Every row carries ``routine`` too: ``{"id", "name"}`` when a firing of one of the caller's
    routines on this mount (``routine_model=``) started the thread, else ``None``.

    With ``child_threads=``, a callable returning the
    [`AGUIHeadlessRunner`][django_ag_ui.AGUIHeadlessRunner] built from the mounts,
    ``DELETE`` cancels the thread's live descendants and unlinks its children in their own
    mounts before it deletes the thread, and ``GET <prefix>threads/<id>/children/`` lists
    its children (``{"children": [...]}``), each with ``thread_id``, ``agent_id`` (the
    child's scope) and its status keys.
    """

    def __init__(
        self,
        store: ConversationStore,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
        config: AGUIConfig | None = None,
        thread_activity_source: ThreadActivitySource | None = None,
        run_store: RunStore | None = None,
        mount: MountIdentity | None = None,
        routine_model: Any = None,
        child_threads: Callable[[], ChildThreads] | None = None,
        resumable: bool = False,
    ) -> None:
        self._store = store
        self._resumable = resumable
        self._routine_model = routine_model
        self._child_threads = child_threads
        self._run_store = run_store
        self._mount = mount if mount is not None else MountIdentity()
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._thread_activity_source = thread_activity_source
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # Load-bearing here: rename is PATCH and delete is DELETE, both checked by CSRF.
        warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        markcoroutinefunction(cast("Any", self))

    async def __call__(
        self, request: HttpRequest, thread_id: str | None = None, action: str | None = None
    ) -> HttpResponseBase:
        # First, so ``request.user`` is materialized before the store's loop-safe calls.
        deny = await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        try:
            if thread_id is None:
                return await self._list(request)
            if action == "children":
                return await self._children(request, thread_id)
            return await self._detail(request, thread_id)
        except AnonymousOperationError:
            return auth_error_response(403)
        except IdentifierTooLongError as error:
            return JsonResponse({"error": str(error)}, status=400)

    async def _list(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        limit = _effective_limit(request, self._config.thread_list_limit)
        return await fresh_list_response(
            request,
            lambda: self.list_body(request, limit),
            heartbeat_seconds=self._config.heartbeat_seconds,
        )

    async def list_body(self, request: HttpRequest, limit: int) -> dict[str, Any]:
        """The ``threads/`` body for an already authorized ``request``, at most ``limit`` rows."""
        metas = await self._store.list(request=request, limit=limit)
        named = await self._routines({m.routine_id for m in metas if m.routine_id}, request)
        rows = [_meta_to_json(m) | {"routine": named.get(m.routine_id or "")} for m in metas]
        if self._run_store is not None:
            ids = [meta.thread_id for meta in metas]
            states = await self._run_store.get_many(ids, request=request)
            agent_id = {} if self._mount.id is None else {"agent_id": self._mount.id}
            for row in rows:
                state = states.get(row["thread_id"])
                row.update(agent_id, **thread_status_fields(state, resumable=self._resumable))
        body: dict[str, Any] = {"threads": rows}
        mount = self._mount
        if (mount.id, mount.name, mount.description) != (None, None, None):
            body["agent"] = {"id": mount.id, "name": mount.name, "description": mount.description}
        return body

    async def _routines(self, ids: set[str], request: HttpRequest) -> dict[str, dict[str, str]]:
        """Each of ``ids`` that is one of the caller's routines on this mount, named."""
        if self._routine_model is None:
            return {}
        fired = self._routine_model._default_manager.filter(
            principal_id=getattr(getattr(request, "user", None), "pk", None),
            agent_id=self._mount.id,
            pk__in=_valid_pks(self._routine_model, ids),
        )
        return {str(r.pk): {"id": str(r.pk), "name": r.name} async for r in fired}

    async def _detail(self, request: HttpRequest, thread_id: str) -> HttpResponseBase:
        if request.method == "GET":
            conversation = await self._store.load(thread_id, request=request)
            if conversation is None:
                return JsonResponse({"error": "not found"}, status=404)
            # Parsed on the way out: older rows hold Python field names, not the wire keys.
            messages = messages_from_jsonable(conversation.messages)
            messages = await self._merge_activities(thread_id, messages, request)
            return JsonResponse(
                {
                    "thread_id": conversation.thread_id,
                    "messages": messages_to_jsonable(messages),
                }
            )
        if request.method == "PATCH":
            return await self._rename(request, thread_id)
        if request.method == "DELETE":
            if self._child_threads is None:
                await self._store.delete(thread_id, request=request)
            else:
                runner = self._child_threads()
                try:
                    await runner.delete_thread(self._agent_id(), thread_id, request.user)
                except LookupError as error:
                    return JsonResponse({"error": str(error)}, status=409)
            return HttpResponse(status=204)
        return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])

    async def _children(self, request: HttpRequest, thread_id: str) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        # test_the_children_action_without_child_threads_is_not_found, ..._does_not_own_...
        if self._child_threads is None or not await self._store.exists(thread_id, request=request):
            return JsonResponse({"error": "not found"}, status=404)
        children = await self._child_threads().children(self._agent_id(), thread_id, request.user)
        rows = [
            {"thread_id": child.thread.thread_id, "agent_id": child.thread.scope}
            | _child_status(child)
            for child in children
        ]
        return JsonResponse({"children": rows})

    def _agent_id(self) -> str:
        assert self._mount.id is not None
        return self._mount.id

    async def _merge_activities(
        self, thread_id: str, messages: list[Message], request: HttpRequest
    ) -> list[Message]:
        """``messages`` with the source's activities put back, or unchanged."""
        if self._thread_activity_source is None:
            return messages
        activities = await self._thread_activity_source.activities_for(
            thread_id, messages=messages, request=request
        )
        return _with_activities(messages, activities)

    async def _rename(self, request: HttpRequest, thread_id: str) -> HttpResponseBase:
        title = _parse_title(request)
        if title is None:
            return JsonResponse({"error": "a non-empty 'title' is required"}, status=400)
        # Metadata-only probe, so a missing or cross-owner thread is a 404, not a silent no-op.
        if not await self._store.exists(thread_id, request=request):
            return JsonResponse({"error": "not found"}, status=404)
        await self._store.rename(thread_id, title, request=request)
        return JsonResponse({"thread_id": thread_id, "title": title})


def _with_activities(
    messages: list[Message], activities: Sequence[ThreadActivity]
) -> list[Message]:
    """``messages`` with each activity materialised in at its anchor."""
    order: list[str] = []
    anchors: dict[str, str | None] = {}
    events: dict[str, ActivitySnapshotEvent] = {}
    known = {message.id for message in messages}
    for activity in activities:
        message_id = activity.event.message_id
        if message_id not in events:
            order.append(message_id)
            anchor = activity.after_message_id
            anchors[message_id] = anchor if anchor in known else None
        events[message_id] = activity.event
    trailing: list[Message] = []
    following: dict[str, list[Message]] = {}
    for message_id in order:
        materialised = _as_activity_message(events[message_id])
        anchor = anchors[message_id]
        if anchor is None:
            trailing.append(materialised)
        else:
            following.setdefault(anchor, []).append(materialised)
    merged: list[Message] = []
    for message in messages:
        merged.append(message)
        merged.extend(following.get(message.id, ()))
    merged.extend(trailing)
    return merged


def _as_activity_message(event: ActivitySnapshotEvent) -> ActivityMessage:
    """The transcript message a live ``ACTIVITY_SNAPSHOT`` becomes in the client."""
    return ActivityMessage(
        id=event.message_id,
        activity_type=event.activity_type,
        content=event.content,
    )


def _child_status(child: ChildSession) -> dict[str, Any]:
    """A thread row's status keys for a child; an index holds no read time, so none is sent."""
    state = ThreadRunState(child.thread.thread_id, child.status, child.reason, child.waiting)
    fields = thread_status_fields(state)
    del fields["last_read_at"]
    return fields


def _meta_to_json(meta: ConversationMeta) -> dict[str, Any]:
    """The wire shape for one thread row — owner_id stays server-side."""
    return {
        "thread_id": meta.thread_id,
        "title": meta.title,
        "updated_at": meta.updated_at.isoformat() if meta.updated_at is not None else None,
        "preview": meta.preview,
        "parent": None if meta.parent is None else dataclasses.asdict(meta.parent),
    }


def _parse_title(request: HttpRequest) -> str | None:
    """The stripped, capped, non-empty ``title`` from a JSON PATCH body, else ``None``."""
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError, RecursionError):
        return None
    title = body.get("title") if isinstance(body, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()[:_MAX_TITLE_LEN]
    return None


def _effective_limit(request: HttpRequest, cap: int) -> int:
    """The thread-list cap for this request: ``?limit`` clamped to the setting."""
    raw = request.GET.get("limit")
    if raw is None:
        return cap
    try:
        requested = int(raw)
    except (TypeError, ValueError):
        return cap
    if requested < 1:
        return cap
    return min(requested, cap)


__all__ = ["ThreadsView"]
