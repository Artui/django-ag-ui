from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMetaList


@runtime_checkable
class ConversationStore(Protocol):
    """Pluggable server-side persistence for AG-UI conversations.

    Passed to ``AGUIServer(conversation_store=...)``. The package ships a no-op
    default (``NullConversationStore`` — the server stays stateless) and a
    session-backed implementation; projects supply their own (a DB model, Redis,
    …). All methods are async so an implementation can use the async ORM or a
    network backend.

    Threads key by ``(owner_id, thread_id)``, so two endpoints sharing a store
    share one user's thread list. Wrap with
    :class:`~django_ag_ui.persistence.scoped_conversation_store.ScopedConversationStore`
    to partition them.

    ``list`` returns owner-scoped *metadata only* (no message bodies) for the
    thread drawer, capped at ``limit`` rows (``None`` = the store's own default);
    a store that can't enumerate (the stateless default) returns an empty list.
    ``exists`` is a cheap owner-scoped presence check — no message body loaded —
    so a rename / probe doesn't deserialize a whole thread just to 404. ``rename``
    sets a thread's display title (a store that can't persist one is a no-op).
    """

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None: ...
    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None: ...
    async def delete(self, thread_id: str, *, request: HttpRequest) -> None: ...
    async def list(
        self, *, request: HttpRequest, limit: int | None = None
    ) -> ConversationMetaList: ...
    async def exists(self, thread_id: str, *, request: HttpRequest) -> bool: ...
    async def rename(self, thread_id: str, title: str, *, request: HttpRequest) -> None: ...


__all__ = ["ConversationStore"]
