from __future__ import annotations

from collections.abc import Sequence

from ag_ui.core import ActivityMessage, Message, UserMessage


def with_stored_activities(posted: Sequence[Message], stored: Sequence[Message]) -> list[Message]:
    """``posted`` with the stored activities a client left out, where the posted turns agree."""
    if any(isinstance(message, ActivityMessage) for message in posted):
        return list(posted)
    turns = [message for message in stored if not isinstance(message, ActivityMessage)]
    agreed = 0
    while agreed < min(len(turns), len(posted)) and turns[agreed].id == posted[agreed].id:
        agreed += 1
    out: list[Message] = []
    seen = 0
    for index, message in enumerate(stored):
        if not isinstance(message, ActivityMessage):
            if seen == agreed:
                break
            out.append(posted[seen])
            seen += 1
            continue
        after = next((m for m in stored[index + 1 :] if not isinstance(m, ActivityMessage)), None)
        if after is None or seen < agreed or isinstance(after, UserMessage):
            out.append(message)
    return [*out, *posted[agreed:]]


__all__ = ["with_stored_activities"]
