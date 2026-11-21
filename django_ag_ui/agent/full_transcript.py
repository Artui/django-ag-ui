from __future__ import annotations

from collections.abc import Collection, Sequence

from ag_ui.core import Message, UserMessage


def full_transcript(
    kept: Sequence[Message], row: Sequence[Message], *, resubmitted: Collection[str] = ()
) -> list[Message]:
    """The row up to a resubmitted prompt, or to the last message it shares with ``kept``."""
    positions = {message.id: index for index, message in enumerate(row)}
    cut = next(
        (i for i, m in enumerate(row) if isinstance(m, UserMessage) and m.id in resubmitted), None
    )
    if cut is not None:
        return list(row[:cut])
    shared = [index for index, message in enumerate(kept) if message.id in positions]
    if not shared:
        return list(kept)
    last = max(shared, key=lambda index: positions[kept[index].id])
    tail = [message for message in kept[last + 1 :] if message.id not in positions]
    return [*row[: positions[kept[last].id] + 1], *tail]


__all__ = ["full_transcript"]
