"""``lend_stored_ids`` — name a dumped history the way the stored conversation named it."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence

from ag_ui.core import ActivityMessage, AssistantMessage, Message, ReasoningMessage, ToolMessage


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
      the text as it streamed, where the dump joins a message's parts with one);
    - an activity message by its position within its ``activity_type``, with
      its content as the check (below).

    **In order and per kind**, never by lookup: each kind walks the row once,
    and a message takes the first row message of its kind at or after the last
    one taken that has the same key. The snapshot is not a literal prefix of
    the row -- a client system message the run stripped, a failed source run
    stored from its transcript, later runs extending the row -- and answers
    repeat their text, so a lookup would hand a later "ok" the id of an earlier
    one. A message no row message matches keeps its dumped id; so a row that
    was deleted, overwritten or diverged lends nothing it does not hold.

    **Activities pair as a prefix**, per ``activity_type``, because an activity
    is not told apart by anything a walk could skip to. Take the N activities
    of one type that ``messages`` dumps: the row must hold at least N of that
    type, the first N of them pair with the dumped ones by position, and every
    pair's content must be equal, as the row holds it and as the dump carries it
    -- both with the ``canonical_system`` key an OpenAI compaction is stored
    with, so the comparison is a plain ``==``. When all of that holds each
    dumped activity takes its pair's id; otherwise every activity of that type
    keeps its dumped id. The row may hold more than the snapshot does (an
    edit-and-resubmit, a resume from an earlier run), so equal counts are not
    required, but it cannot lend an id from another position
    (``test_an_activity_is_not_lent_an_id_from_another_position``) or to one of
    several it holds too few for
    (``test_an_activity_is_not_lent_an_id_when_the_row_holds_fewer``). The cost
    is a failed source run's activity: its compaction is in the snapshot and
    the row does not hold it, so the pairing shifts or fails and its whole type
    gets fresh ids. Fresh ids, never wrong ones. The pairing is judged whole:
    without the at-least-N check ``test_an_activity_is_not_lent_an_id_when_the_row_holds_fewer``
    fails, without the positional pairing
    ``test_an_activity_is_not_lent_an_id_from_another_position``, without the
    content check ``test_an_activity_whose_content_differs_keeps_its_id``, and
    without the per-type grouping ``test_a_type_whose_pairing_fails_keeps_every_dumped_id``
    and ``test_an_activity_matches_within_its_own_type_only``.

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
    """The kind a message is matched within, and the key it is matched by.

    A key is compared, never hashed. An activity has none: it is paired by
    position, see ``_paired_activities``.
    """
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
    """The row's id for each dumped activity, by its index in ``messages``.

    Only the activities of a type whose first N stored activities (N being the
    dumped count) all hold the dumped content, in the dumped order, appear.
    """
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
