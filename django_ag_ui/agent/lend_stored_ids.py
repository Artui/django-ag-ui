from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence

from ag_ui.core import (
    ActivityMessage,
    AssistantMessage,
    Message,
    ReasoningMessage,
    ToolMessage,
    UserMessage,
)


def lend_stored_ids(
    messages: Sequence[Message], stored: Sequence[Message], *, reserved: Collection[str] = ()
) -> list[Message]:
    """``messages``, each under the id ``stored`` holds for the same message, where it holds one."""
    rows: dict[str, list[tuple[object, str, int]]] = {}
    for position, message in enumerate(stored):
        keyed = _kind_and_key(message)
        if keyed is not None:
            rows.setdefault(keyed[0], []).append((keyed[1], message.id, position))
    cursors: Counter[str] = Counter()
    activities = _paired_activities(messages, stored)
    taken = set(reserved)
    carried = {message.id for message in messages} - _freed(messages, activities, taken)
    held = {message.id for message in stored}
    out: list[Message] = []
    for position, message in enumerate(messages):
        if isinstance(message, ActivityMessage):
            lent = activities.get(position)
        elif isinstance(message, UserMessage):
            lent = _user_match(rows, cursors, message, held)
        elif (keyed := _kind_and_key(message)) is None:
            lent = None
        else:
            lent = _next_match(rows, cursors, *keyed, anchors=keyed[0] in _ANCHORED)
        if lent is None or lent in taken or lent in carried:
            out.append(message)
            continue
        taken.add(lent)
        out.append(message.model_copy(update={"id": lent}))
    return out


_ANCHORED = frozenset({"tool", "calls"})


def _freed(messages: Sequence[Message], activities: dict[int, str], taken: set[str]) -> set[str]:
    """The ids activities give up for stored ones, which a merged request had put on them."""
    ids = Counter(message.id for message in messages)
    return {
        messages[position].id
        for position, lent in activities.items()
        if lent not in ids and lent not in taken
    }


def _kind_and_key(message: Message) -> tuple[str, object] | None:
    """The kind a message is matched within, and the key it is matched by."""
    if isinstance(message, UserMessage):
        return "user", _prompt_key(message)
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


def _user_match(
    rows: dict[str, list[tuple[object, str, int]]],
    cursors: Counter[str],
    message: UserMessage,
    held: set[str],
) -> str | None:
    """A fresh-id prompt matched by content; one under a stored id keeps it and its place."""
    if message.id in held:
        return _next_match(rows, cursors, "user", message.id, by_id=True, anchors=True)
    return _next_match(rows, cursors, "user", _prompt_key(message))


def _prompt_key(message: UserMessage) -> str:
    return message.model_dump_json(include={"content"})


def _next_match(
    rows: dict[str, list[tuple[object, str, int]]],
    cursors: Counter[str],
    kind: str,
    key: object,
    *,
    by_id: bool = False,
    anchors: bool = False,
) -> str | None:
    """The id of the next row message of ``kind`` with ``key`` (or that id), consuming up to it."""
    row = rows.get(kind, [])
    for index in range(cursors[kind], len(row)):
        if row[index][1 if by_id else 0] == key:
            cursors[kind] = index + 1
            if anchors:
                _move_past(rows, cursors, row[index][2])
            return row[index][1]
    return None


def _move_past(
    rows: dict[str, list[tuple[object, str, int]]], cursors: Counter[str], position: int
) -> None:
    """Move every kind's cursor past row ``position``: an exact match fixes where the dump is."""
    for kind, row in rows.items():
        while cursors[kind] < len(row) and row[cursors[kind]][2] <= position:
            cursors[kind] += 1


def _flat(text: str | None) -> str:
    return (text or "").replace("\n", "")


__all__ = ["lend_stored_ids"]
