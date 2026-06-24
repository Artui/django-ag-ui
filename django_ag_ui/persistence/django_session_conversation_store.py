from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest

from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import (
    ConversationMeta,
    ConversationMetaList,
)
from django_ag_ui.persistence.utils import (
    derive_preview,
    derive_title,
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

    async def list(self, *, request: HttpRequest) -> ConversationMetaList:
        return await sync_to_async(self._list)(request)

    async def rename(self, thread_id: str, title: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._rename)(thread_id, title, request)

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
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        request.session[_SESSION_KEY] = store
        request.session.modified = True

    def _delete(self, thread_id: str, request: HttpRequest) -> None:
        store = request.session.get(_SESSION_KEY, {})
        if thread_id in store:
            del store[thread_id]
            request.session[_SESSION_KEY] = store
            request.session.modified = True

    def _list(self, request: HttpRequest) -> ConversationMetaList:
        store = request.session.get(_SESSION_KEY, {})
        return [_meta(thread_id, raw) for thread_id, raw in store.items()]

    def _rename(self, thread_id: str, title: str, request: HttpRequest) -> None:
        store = request.session.get(_SESSION_KEY, {})
        if thread_id in store:
            store[thread_id]["title"] = title
            request.session[_SESSION_KEY] = store
            request.session.modified = True


def _meta(thread_id: str, raw: dict[str, Any]) -> ConversationMeta:
    messages = messages_from_jsonable(raw["messages"])
    return ConversationMeta(
        thread_id=thread_id,
        # An explicit rename (stored "title") wins over the derived title.
        title=raw.get("title") or derive_title(messages),
        updated_at=_parse_iso(raw.get("updated_at")),
        preview=derive_preview(messages),
        owner_id=raw.get("owner_id"),
    )


def _parse_iso(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp; ``None`` for entries saved before tracking."""
    return datetime.fromisoformat(value) if value else None


__all__ = ["DjangoSessionConversationStore"]
