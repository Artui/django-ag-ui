from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from ag_ui.core import ActivityMessage, ActivitySnapshotEvent, Message
from asgiref.sync import markcoroutinefunction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.response import HttpResponseBase
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.types.conversation_meta import ConversationMeta
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.types.thread_activity import ThreadActivity
from django_ag_ui.persistence.types.thread_activity_source import ThreadActivitySource
from django_ag_ui.persistence.utils import messages_from_jsonable, messages_to_jsonable
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated

# The model stores back ``title`` with ``CharField(max_length=255)``; cap the
# rename here (truncate — a title is cosmetic) so an over-long PATCH is a clean
# save on every backend rather than a ``DataError`` on a strict database.
_MAX_TITLE_LEN = 255


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
    ) -> None:
        self._store = store
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
        self, request: HttpRequest, thread_id: str | None = None
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
            return await self._detail(request, thread_id)
        except AnonymousOperationError:
            return auth_error_response(403)

    async def _list(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        limit = _effective_limit(request, self._config.thread_list_limit)
        metas = await self._store.list(request=request, limit=limit)
        return JsonResponse({"threads": [_meta_to_json(meta) for meta in metas]})

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
            await self._store.delete(thread_id, request=request)
            return HttpResponse(status=204)
        return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])

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


def _meta_to_json(meta: ConversationMeta) -> dict[str, Any]:
    """The wire shape for one thread row — owner_id stays server-side."""
    return {
        "thread_id": meta.thread_id,
        "title": meta.title,
        "updated_at": meta.updated_at.isoformat() if meta.updated_at is not None else None,
        "preview": meta.preview,
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
