from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence

from ag_ui.core import ActivityMessage, AssistantMessage, Message, ReasoningMessage, ToolMessage


def lend_stored_ids(
    messages: Sequence[Message], stored: Sequence[Message], *, reserved: Collection[str] = ()
) -> list[Message]:
    """``messages``, each under the id ``stored`` holds for the same message, where it holds one."""
    rows: dict[str, list[tuple[object, str]]] = {}
    for message in stored:
        keyed = _kind_and_key(message)
        if keyed is not None:
            rows.setdefault(keyed[0], []).append((keyed[1], message.id))
    cursors: Counter[str] = Counter()
    activities = _paired_activities(messages, stored)
    taken = set(reserved)
    carried = {message.id for message in messages}
    out: list[Message] = []
    for position, message in enumerate(messages):
        if isinstance(message, ActivityMessage):
            lent = activities.get(position)
        else:
            keyed = _kind_and_key(message)
            lent = None if keyed is None else _next_match(rows, cursors, *keyed)
        if lent is None or lent in taken or lent in carried:
            out.append(message)
            continue
        taken.add(lent)
        out.append(message.model_copy(update={"id": lent}))
    return out


def _kind_and_key(message: Message) -> tuple[str, object] | None:
    """The kind a message is matched within, and the key it is matched by."""
    if isinstance(message, ToolMessage):
        return "tool", message.tool_call_id
    if isinstance(message, AssistantMessage):
        if message.tool_calls:
            return "calls", tuple(call.id for call in message.tool_calls)
        return "text", _flat(message.content)
    if isinstance(message, ReasoningMessage):
        return "reasoning", _flat(message.content)
    return None


def _paired_activities(messages: Sequence[Message], stored: Sequence[Message]) -> dict[int, str]:
    """The row's id for each dumped activity, by its index in ``messages``."""
    dumped: dict[str, list[tuple[int, ActivityMessage]]] = {}
    for position, message in enumerate(messages):
        if isinstance(message, ActivityMessage):
            dumped.setdefault(message.activity_type, []).append((position, message))
    held: dict[str, list[ActivityMessage]] = {}
    for message in stored:
        if isinstance(message, ActivityMessage):
            held.setdefault(message.activity_type, []).append(message)
    paired: dict[int, str] = {}
    for activity_type, wanted in dumped.items():
        row = held.get(activity_type, [])[: len(wanted)]
        if len(row) < len(wanted):
            continue
        pairs = list(zip(wanted, row, strict=True))
        if all(found.content == message.content for (_, message), found in pairs):
            paired.update({position: found.id for (position, _), found in pairs})
    return paired


def _next_match(
    rows: dict[str, list[tuple[object, str]]], cursors: Counter[str], kind: str, key: object
) -> str | None:
    """The id of the next row message of ``kind`` with ``key``, consuming the row up to it."""
    row = rows.get(kind, [])
    for index in range(cursors[kind], len(row)):
        if row[index][0] == key:
            cursors[kind] = index + 1
            return row[index][1]
    return None


def _flat(text: str | None) -> str:
    return (text or "").replace("\n", "")


__all__ = ["lend_stored_ids"]
