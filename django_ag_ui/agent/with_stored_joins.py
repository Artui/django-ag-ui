from __future__ import annotations

from collections.abc import Container, Sequence
from typing import Any, TypeGuard

from ag_ui.core import AssistantMessage, Message


def with_stored_joins(messages: Sequence[Message], stored: Sequence[Message]) -> list[Message]:
    """``messages``, each run of text pieces a row stored before the split holds joined as it."""
    joined = {m.content: m for m in stored if _is_text(m)}
    texts = set(joined)
    out: list[Message] = []
    at = 0
    while at < len(messages):
        key, end = _row_at(messages, at, joined, texts)
        if key is None:
            out.append(messages[at])
            at += 1
            continue
        out.append(joined.pop(key))
        at = end
    return out


def _row_at(
    messages: Sequence[Message], at: int, joined: Container[Any], texts: Container[Any]
) -> tuple[str | None, int]:
    """The joined text the pieces from ``at`` spell, and where they end; pieces stored alone win."""
    pieces: list[str] = []
    for end in range(at, len(messages)):
        message = messages[end]
        if not _is_text(message) or message.content in texts:
            break
        pieces.append(message.content or "")
        if (key := "\n".join(pieces)) in joined:
            return key, end + 1
    return None, at


def _is_text(message: Message) -> TypeGuard[AssistantMessage]:
    """Whether ``message`` is an assistant message holding no tool call."""
    return isinstance(message, AssistantMessage) and not message.tool_calls


__all__ = ["with_stored_joins"]
