from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.response import HttpResponseBase

from django_ag_ui.persistence.types.conversation_meta import ConversationMeta
from django_ag_ui.persistence.types.conversation_store import ConversationStore
from django_ag_ui.persistence.utils import messages_to_jsonable
from django_ag_ui.utils import GetUser, aauthorize


class ThreadsView:
    """Owner-scoped thread index endpoint for the chat-history drawer (async, JSON).

    Mounted by :func:`~django_ag_ui.get_urls` with ``threads=<store>`` over the
    same :class:`~django_ag_ui.ConversationStore` the agent view uses:

    - ``GET    <prefix>threads/``       → the user's threads, **metadata only**
      (``{"threads": [...]}``);
    - ``GET    <prefix>threads/<id>/``  → that thread's messages
      (``{"thread_id", "messages"}``);
    - ``DELETE <prefix>threads/<id>/``  → delete the thread (``204``).

    Every operation is scoped to the acting user: the store filters by owner, so
    a thread owned by another user simply isn't found (``404``) — never another
    user's history. The view carries the same authentication seam as
    :class:`~django_ag_ui.DjangoAGUIView` (``require_authenticated`` /
    ``get_user``, sync or async); defaults stay open for parity with the catalog
    views, so lock it down whenever the agent endpoint is locked down. Renaming
    (``PATCH``) is a planned follow-up — any other method returns ``405``.
    """

    def __init__(
        self,
        store: ConversationStore,
        *,
        require_authenticated: bool = False,
        get_user: GetUser | None = None,
    ) -> None:
        self._store = store
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        # Mark this callable instance async so Django awaits ``__call__`` (see
        # DjangoAGUIView for the rationale); the store operations are async.
        markcoroutinefunction(cast("Any", self))

    async def __call__(
        self, request: HttpRequest, thread_id: str | None = None
    ) -> HttpResponseBase:
        # Establish + authorize the acting user first: this materializes
        # ``request.user`` off the event loop, so the store's ``owner_id_for``
        # scoping is loop-safe on the calls below.
        if not await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
        ):
            return JsonResponse({"error": "authentication required"}, status=401)
        if thread_id is None:
            return await self._list(request)
        return await self._detail(request, thread_id)

    async def _list(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        metas = await self._store.list(request=request)
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
        if request.method == "DELETE":
            await self._store.delete(thread_id, request=request)
            return HttpResponse(status=204)
        return HttpResponseNotAllowed(["GET", "DELETE"])


def _meta_to_json(meta: ConversationMeta) -> dict[str, Any]:
    """The wire shape for one thread row — owner_id stays server-side."""
    return {
        "thread_id": meta.thread_id,
        "title": meta.title,
        "updated_at": meta.updated_at.isoformat() if meta.updated_at is not None else None,
        "preview": meta.preview,
    }


__all__ = ["ThreadsView"]
