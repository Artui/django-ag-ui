from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ag_ui.core import Message

_TEXT_PART = "text"
_INLINE_SOURCE = "data"


def strip_binary_content(messages: Sequence[Message]) -> list[Message]:
    """Copies of ``messages`` with inlined file bytes removed."""
    kept: list[Message] = []
    for message in messages:
        content = message.content
        if not isinstance(content, list):
            kept.append(message)
            continue
        parts = [part for part in content if not _is_inline_binary(part)]
        if not parts:
            continue
        if len(parts) == len(content):
            kept.append(message)
            continue
        kept.append(message.model_copy(update={"content": parts}))
    return kept


def _is_inline_binary(part: Any) -> bool:
    """Whether ``part`` carries bytes inline rather than a reference to them."""
    if part.type == _TEXT_PART:
        return False
    return part.source.type == _INLINE_SOURCE


__all__ = ["strip_binary_content"]
