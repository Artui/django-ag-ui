from __future__ import annotations

from dataclasses import dataclass, field

from ag_ui.core import Message


@dataclass(frozen=True)
class Conversation:
    """A persisted conversation, keyed by ``thread_id``.

    ``messages`` are AG-UI ``Message`` objects (the wire shape the client
    speaks), so a store round-trips them verbatim. ``owner_id`` scopes the
    conversation to a user for authorization.
    """

    thread_id: str
    messages: list[Message] = field(default_factory=list)
    owner_id: str | None = None


__all__ = ["Conversation"]
