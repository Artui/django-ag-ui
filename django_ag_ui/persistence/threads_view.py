from __future__ import annotations

import json
from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.response import HttpResponseBase

from django_ag_ui.conf import get_settings
from django_ag_ui.persistence.anonymous_operation_error import AnonymousOperationError
from django_ag_ui.persistence.types.conversation_meta import ConversationMeta
from django_ag_ui.persistence.types.conversation_store import ConversationStore
from django_ag_ui.persistence.utils import messages_to_jsonable
from django_ag_ui.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

# The model stores back ``title`` with ``CharField(max_length=255)``; cap the
# rename here (truncate — a title is cosmetic) so an over-long PATCH is a clean
# save on every backend rather than a ``DataError`` on a strict database.
_MAX_TITLE_LEN = 255


class ThreadsView:
    """Owner-scoped thread index endpoint for the chat-history drawer (async, JSON).

    Mounted by :func:`~django_ag_ui.get_urls` with ``threads=<store>`` over the
    same :class:`~django_ag_ui.ConversationStore` the agent view uses:

    - ``GET    <prefix>threads/``       → the user's threads, **metadata only**
      (``{"threads": [...]}``);
    - ``GET    <prefix>threads/<id>/``  → that thread's messages
      (``{"thread_id", "messages"}``);
    - ``PATCH  <prefix>threads/<id>/``  → rename (body ``{"title": "..."}``);
    - ``DELETE <prefix>threads/<id>/``  → delete the thread (``204``).

    Every operation is scoped to the acting user: the store filters by owner, so
    a thread owned by another user simply isn't found (``404``) — never another
    user's history. The view carries the same authentication seam as
    :class:`~django_ag_ui.DjangoAGUIView` (``require_authenticated`` /
    ``get_user``, sync or async); defaults stay open for parity with the catalog
    views, so lock it down whenever the agent endpoint is locked down.
    """

    def __init__(
        self,
        store: ConversationStore,
        *,
        require_authenticated: bool = False,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
    ) -> None:
        self._store = store
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # Mark this callable instance async so Django awaits ``__call__`` (see
        # DjangoAGUIView for the rationale); the store operations are async.
        markcoroutinefunction(cast("Any", self))

    async def __call__(
        self, request: HttpRequest, thread_id: str | None = None
    ) -> HttpResponseBase:
        # Establish + authorize the acting user first: this materializes
        # ``request.user`` off the event loop, so the store's owner scoping is
        # loop-safe on the calls below.
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
            # A model-backed store refusing an anonymous request (the default
            # unless ``ALLOW_ANONYMOUS``): forbidden, not a server error.
            return auth_error_response(403)

    async def _list(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        limit = _effective_limit(request)
        metas = await self._store.list(request=request, limit=limit)
        return JsonResponse({"threads": [_meta_to_json(meta) for meta in metas]})

    async def _detail(self, request: HttpRequest, thread_id: str) -> HttpResponseBase:
        if request.method == "GET":
            conversation = await self._store.load(thread_id, request=request)
            if conversation is None:
                return JsonResponse({"error": "not found"}, status=404)
            return JsonResponse(
                {
                    "thread_id": conversation.thread_id,
                    "messages": messages_to_jsonable(conversation.messages),
                }
            )
        if request.method == "PATCH":
            return await self._rename(request, thread_id)
        if request.method == "DELETE":
            await self._store.delete(thread_id, request=request)
            return HttpResponse(status=204)
        return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])

    async def _rename(self, request: HttpRequest, thread_id: str) -> HttpResponseBase:
        title = _parse_title(request)
        if title is None:
            return JsonResponse({"error": "a non-empty 'title' is required"}, status=400)
        # Probe existence (metadata only) so a missing / cross-owner thread is a
        # 404 rather than a silent rename of nothing — without deserializing the
        # whole message body just to answer.
        if not await self._store.exists(thread_id, request=request):
            return JsonResponse({"error": "not found"}, status=404)
        await self._store.rename(thread_id, title, request=request)
        return JsonResponse({"thread_id": thread_id, "title": title})


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
    except (ValueError, TypeError):
        return None
    title = body.get("title") if isinstance(body, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()[:_MAX_TITLE_LEN]
    return None


def _effective_limit(request: HttpRequest) -> int:
    """The thread-list cap for this request: ``?limit`` clamped to the setting.

    A missing / non-positive / non-integer ``?limit`` falls back to the configured
    ceiling; a larger one is clamped down to it, so the response is always bounded.
    """
    cap = get_settings().thread_list_limit
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
