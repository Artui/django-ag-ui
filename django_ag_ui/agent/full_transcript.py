from __future__ import annotations

from collections.abc import Sequence

from ag_ui.core import Message


def full_transcript(kept: Sequence[Message], row: Sequence[Message]) -> list[Message]:
    """The row up to the last message ``kept`` shares with it, then ``kept``'s own tail."""
    positions = {message.id: index for index, message in enumerate(row)}
    shared = [index for index, message in enumerate(kept) if message.id in positions]
    if not shared:
        return list(kept)
    last = max(shared, key=lambda index: positions[kept[index].id])
    tail = [message for message in kept[last + 1 :] if message.id not in positions]
    return [*row[: positions[kept[last].id] + 1], *tail]


__all__ = ["full_transcript"]
