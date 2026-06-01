from __future__ import annotations

from asgiref.sync import sync_to_async
from django.http import HttpRequest

from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.utils import (
    messages_from_jsonable,
    messages_to_jsonable,
)

_SESSION_KEY = "django_ag_ui_conversations"


class DjangoSessionConversationStore:
    """Conversation persistence in the Django session (no migration).

    Conversations are namespaced by ``thread_id`` within the logged-in user's
    session, so scoping to the user is implicit and durability spans that
    user's browser session. The batteries-included server-side store; for
    cross-device or audited persistence, supply a model-backed store instead.
    """

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
        return await sync_to_async(self._load)(thread_id, request)

    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None:
        await sync_to_async(self._save)(conversation, request)

    async def delete(self, thread_id: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._delete)(thread_id, request)

    def _load(self, thread_id: str, request: HttpRequest) -> Conversation | None:
        raw = request.session.get(_SESSION_KEY, {}).get(thread_id)
        if raw is None:
            return None
        return Conversation(
            thread_id=thread_id,
            messages=messages_from_jsonable(raw["messages"]),
            owner_id=raw.get("owner_id"),
        )

    def _save(self, conversation: Conversation, request: HttpRequest) -> None:
        store = request.session.get(_SESSION_KEY, {})
        store[conversation.thread_id] = {
            "messages": messages_to_jsonable(conversation.messages),
            "owner_id": conversation.owner_id,
        }
        request.session[_SESSION_KEY] = store
        request.session.modified = True

    def _delete(self, thread_id: str, request: HttpRequest) -> None:
        store = request.session.get(_SESSION_KEY, {})
        if thread_id in store:
            del store[thread_id]
            request.session[_SESSION_KEY] = store
            request.session.modified = True


__all__ = ["DjangoSessionConversationStore"]
