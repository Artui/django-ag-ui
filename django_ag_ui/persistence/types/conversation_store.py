from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMetaList


@runtime_checkable
class ConversationStore(Protocol):
    """Pluggable server-side persistence for AG-UI conversations.

    Resolved from ``DJANGO_AG_UI["CONVERSATION_STORE"]``. The package ships a
    no-op default (``NullConversationStore`` — the server stays stateless) and a
    session-backed implementation; projects supply their own (a DB model, Redis,
    …) by pointing the setting at a dotted path. All methods are async so an
    implementation can use the async ORM or a network backend.

    ``list`` returns owner-scoped *metadata only* (no message bodies) for the
    thread drawer; a store that can't enumerate (the stateless default) returns
    an empty list.
    """

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None: ...
    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None: ...
    async def delete(self, thread_id: str, *, request: HttpRequest) -> None: ...
    async def list(self, *, request: HttpRequest) -> ConversationMetaList: ...


__all__ = ["ConversationStore"]
