from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias


@dataclass(frozen=True)
class ConversationMeta:
    """Lightweight metadata for one conversation — the thread-drawer row shape.

    Returned by :meth:`ConversationStore.list <django_ag_ui.ConversationStore>`
    so a thread list stays cheap: it carries **no message bodies**. ``title``
    defaults to a truncation of the first user message (unless a store records an
    explicit rename); ``preview`` is a one-line excerpt of the latest message;
    ``updated_at`` is when the conversation last changed, or ``None`` when the
    store doesn't track it. ``owner_id`` scopes the conversation to a user and is
    not surfaced on the wire.
    """

    thread_id: str
    title: str
    updated_at: datetime | None = None
    preview: str = ""
    owner_id: str | None = None


# Return type for ``ConversationStore.list``. Aliased because a store names that
# method ``list``, which shadows the builtin inside the class body and would
# break a bare ``list[ConversationMeta]`` return annotation.
ConversationMetaList: TypeAlias = list[ConversationMeta]


__all__ = ["ConversationMeta", "ConversationMetaList"]
