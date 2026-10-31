"""``lend_stored_ids`` — name a dumped history the way the stored conversation named it."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Hashable, Sequence

from ag_ui.core import AssistantMessage, Message, ReasoningMessage, ToolMessage


def lend_stored_ids(
    messages: Sequence[Message], stored: Sequence[Message], *, reserved: Collection[str] = ()
) -> list[Message]:
    """``messages``, each under the id ``stored`` holds for the same message, where it holds one.

    ``messages`` is a resumed run's history, dumped from the step store's
    snapshot. pydantic-ai restores on a dump only the id of the last client
    message folded into each loaded message, so everything a run generated, and
    a tool result posted ahead of another, comes back under a fresh id on every
    save -- and a host that keyed a rating by the earlier id loses it.
    ``stored`` is the source thread's conversation row, which holds the ids
    those messages were stored and streamed under.

    Each message is matched by the most exact key it has, the keys
    ``RunTranscript.with_streamed_ids`` uses for a live run:

    - a tool message by its ``tool_call_id``;
    - an assistant message with tool calls by its calls' ids;
    - a text-only assistant message, or a reasoning message, by its text with
      every newline removed (a row stored from a failed run's transcript holds
      the text as it streamed, where the dump joins a message's parts with one).

    **In order and per kind**, never by lookup: each kind walks the row once,
    and a message takes the first row message of its kind at or after the last
    one taken that has the same key. The snapshot is not a literal prefix of
    the row -- a client system message the run stripped, a failed source run
    stored from its transcript, later runs extending the row -- and answers
    repeat their text, so a lookup would hand a later "ok" the id of an earlier
    one. A message no row message matches keeps its dumped id; so a row that
    was deleted, overwritten or diverged lends nothing it does not hold.

    **No id is stored twice.** A row id is lent once, never one in
    ``reserved`` (the ids of the new turn stored after these messages), and
    never one any of ``messages`` already carries -- which also leaves a
    message already under its stored id as it is. The guard below is one branch
    arc over three conditions: without ``lent is None``,
    ``test_a_message_no_stored_message_matches_keeps_its_id`` fails; without
    ``lent in taken``, ``test_a_row_id_is_lent_once`` and
    ``test_a_reserved_id_is_not_lent``; without ``lent in carried``,
    ``test_an_id_another_message_carries_is_not_lent``.
    """
    rows: dict[str, list[tuple[Hashable, str]]] = {}
    for message in stored:
        keyed = _kind_and_key(message)
        if keyed is not None:
            rows.setdefault(keyed[0], []).append((keyed[1], message.id))
    cursors: Counter[str] = Counter()
    taken = set(reserved)
    carried = {message.id for message in messages}
    out: list[Message] = []
    for message in messages:
        keyed = _kind_and_key(message)
        lent = None if keyed is None else _next_match(rows, cursors, *keyed)
        if lent is None or lent in taken or lent in carried:
            out.append(message)
            continue
        taken.add(lent)
        out.append(message.model_copy(update={"id": lent}))
    return out


def _kind_and_key(message: Message) -> tuple[str, Hashable] | None:
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


def _next_match(
    rows: dict[str, list[tuple[Hashable, str]]], cursors: Counter[str], kind: str, key: Hashable
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
