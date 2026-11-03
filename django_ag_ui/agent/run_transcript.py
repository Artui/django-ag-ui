from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeGuard, TypeVar

from ag_ui.core import (
    ActivityDeltaEvent,
    ActivityMessage,
    ActivitySnapshotEvent,
    AssistantMessage,
    BaseEvent,
    FunctionCall,
    Message,
    ReasoningMessage,
    ReasoningMessageContentEvent,
    ReasoningStartEvent,
    TextMessageContentEvent,
    TextMessageStartEvent,
    ToolCall,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    ToolMessage,
)

from django_ag_ui.agent.stamp_outcome import OUTCOME_FIELD, stamp_outcome

_T = TypeVar("_T")
_S = TypeVar("_S")
_D = TypeVar("_D")


@dataclass
class _Draft:
    """An assistant message under construction."""

    message_id: str
    text: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class _PendingCall:
    """A tool call whose arguments are still streaming."""

    name: str
    parent_message_id: str | None
    args: list[str] = field(default_factory=list)


@dataclass
class _Activity:
    """A streamed activity: its type, and its content as the client last had it.

    The content is ``None`` once an ``ACTIVITY_DELTA`` changed it: the delta is a
    JSON patch, and rather than apply it here the activity is left with no
    content to compare, which pairs it with nothing.
    """

    activity_type: str
    content: dict[str, Any] | None


class RunTranscript:
    """Accumulate a run's AG-UI events into the messages streamed so far.

    Pydantic-AI only hands the server a run's messages when the run
    *finishes* (the adapter's ``on_complete``); a client that disconnects
    mid-run leaves no result to read. This observer reconstructs the
    assistant's side of the exchange from the event stream itself, so a
    cancelled run can still persist the truncated conversation: completed
    text, completed tool calls and their results, plus any partially
    streamed text. Partially streamed *tool calls* are dropped — half a
    JSON arguments string is not a usable record.

    A run that *does* finish is stored from pydantic-ai's own dump instead, which
    keeps what the events cannot carry, and the transcript lends that dump the
    ids the stream announced -- see ``with_streamed_ids``.
    """

    def __init__(self) -> None:
        self._items: list[_Draft | ToolMessage] = []
        self._pending_calls: dict[str, _PendingCall] = {}
        # Kept apart from the drafts because a draft is keyed by the call id when
        # a call names no parent, and that key is not an id the stream announced.
        self._call_parents: list[tuple[str, str | None]] = []
        # Each streamed reasoning message's deltas, by id, in the order announced.
        self._reasoning: dict[str, list[str]] = {}
        # Each streamed activity, by id, in the order announced.
        self._activities: dict[str, _Activity] = {}

    async def observe(self, stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
        """Pass ``stream`` through unchanged, recording every event."""
        async for event in stream:
            self.add(event)
            yield event

    def add(self, event: BaseEvent) -> None:
        """Record one AG-UI event; events that carry no message content are ignored."""
        if isinstance(event, TextMessageStartEvent):
            self._draft_for(event.message_id)
        elif isinstance(event, TextMessageContentEvent):
            self._draft_for(event.message_id).text.append(event.delta)
        elif isinstance(event, ToolCallStartEvent):
            self._pending_calls[event.tool_call_id] = _PendingCall(
                name=event.tool_call_name,
                parent_message_id=event.parent_message_id,
            )
            self._call_parents.append((event.tool_call_id, event.parent_message_id))
        elif isinstance(event, ToolCallArgsEvent):
            self._pending_calls[event.tool_call_id].args.append(event.delta)
        elif isinstance(event, ToolCallEndEvent):
            pending = self._pending_calls.pop(event.tool_call_id)
            call = ToolCall(
                id=event.tool_call_id,
                type="function",
                function=FunctionCall(name=pending.name, arguments="".join(pending.args)),
            )
            # The protocol allows a call without a parent message; key the
            # draft by the call id then, so the call still lands in order.
            self._draft_for(pending.parent_message_id or event.tool_call_id).tool_calls.append(
                call,
            )
        elif isinstance(event, ToolCallResultEvent):
            self._items.append(_tool_message(event))
        elif isinstance(event, ReasoningStartEvent):
            # The start rather than the message start: a thinking part that
            # carries only a signature opens and closes reasoning with no text
            # message inside, and is still dumped as a reasoning message
            # (``test_a_signature_only_reasoning_still_pairs``).
            self._reasoning.setdefault(event.message_id, [])
        elif isinstance(event, ReasoningMessageContentEvent):
            self._reasoning.setdefault(event.message_id, []).append(event.delta)
        elif isinstance(event, ActivitySnapshotEvent):
            self._snapshot(event)
        elif isinstance(event, ActivityDeltaEvent):
            self._activities.setdefault(
                event.message_id, _Activity(event.activity_type, None)
            ).content = None

    def messages(self) -> list[Message]:
        """The reconstructed messages, in stream order.

        An open text buffer is included (partial text is meaningful to a
        reader); a draft that accumulated neither text nor tool calls is
        skipped rather than persisted as an empty message.
        """
        out: list[Message] = []
        for item in self._items:
            if isinstance(item, ToolMessage):
                out.append(item)
                continue
            content = "".join(item.text) or None
            tool_calls = list(item.tool_calls) or None
            if content is None and tool_calls is None:
                continue
            out.append(
                AssistantMessage(
                    id=item.message_id,
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls,
                ),
            )
        return out

    def with_streamed_ids(self, messages: Sequence[Message]) -> list[Message]:
        """``messages``, each under the id the stream announced for it.

        ``messages`` is pydantic-ai's dump of the run this transcript observed.
        The dump mints a fresh id for every message it did not load from the
        client, so a completed run stored as dumped names each answer
        differently from the ``TEXT_MESSAGE_START`` the client keyed it by --
        and differently from the cancelled and failed exits, which store
        ``messages()`` and so already carry the streamed ids. The dump is kept
        rather than replaced by ``messages()`` because it holds what the events
        never carry: reasoning and its signatures, a tool call's kind, a result's
        ``error``, compaction and file activities.

        Each message is matched by the most exact key it has:

        - a tool message by its ``tool_call_id``, to the ``TOOL_CALL_RESULT``
          that carried it;
        - an assistant message with tool calls by the ``parent_message_id``
          its calls streamed under, only when they all name the same one --
          which is also what the empty text message a tool-call-only response
          opens is announced as;
        - a text-only assistant message, and a reasoning message, by position
          among the streamed ones of its kind, only when the two counts agree
          *and* every pair holds the same text;
        - an activity message by position among the streamed activities of its
          ``activity_type``, by the same rule, with its content for the text.
          An activity an ``ACTIVITY_DELTA`` changed holds no content to compare,
          so its type keeps the dumped ids.

        **No message is ever lent another message's id**, and the text is what
        holds that: an assistant message takes a streamed id only when it holds
        the text that id streamed. Equal counts alone are not enough, because
        the dump and the stream can disagree in opposite directions and cancel
        out. A response of text, thinking, then tool calls streams as one
        message and dumps as two, one of them text-only; a file part between two
        texts streams as two messages and dumps as one, since the dump drops the
        file without a flush; a tool can stream a text message of its own that
        the dump never holds. One surplus on each side leaves the counts equal
        and every position after the first shifted onto a neighbour, which is
        worse than no id at all: a rating keyed by one answer would land on
        another after a reload. Text is compared with every newline removed,
        because the dump joins consecutive text parts with one where the stream
        runs them together; two answers that differ only in their newlines would
        pass for one another, and both would have to sit at the same position
        of an otherwise shifted run.

        A message no key reaches keeps its dumped id, and so does one whose
        streamed id an earlier message already took -- a response whose calls a
        thinking part splits in two dumps as two assistant messages under one
        parent, and one stored id naming two messages would be worse than a
        fresh id naming one. Withheld reasoning (``FORWARD_REASONING`` off) is
        the common case of the first: the client was announced no id for it.
        Any disagreement in a kind's pairing drops the streamed ids of the whole
        run for that kind, not only of the response it happened in: the stream
        carries no event for a response boundary, so there is nothing to scope
        the fallback by. Calls and results are matched in order per id, so a
        retry that reuses a call id pairs off in sequence.

        The guard below is one branch arc over two conditions. Without
        ``streamed is None``, a message no key reaches is stored with no id and
        ``test_withheld_reasoning_is_stored_under_an_id_of_its_own`` fails;
        without ``streamed in claimed``,
        ``test_a_streamed_id_is_lent_to_one_message_only`` fails.
        """
        results: dict[str, deque[str]] = {}
        for item in self._items:
            if isinstance(item, ToolMessage):
                results.setdefault(item.tool_call_id, deque()).append(item.id)
        parents: dict[str, deque[str | None]] = {}
        for call_id, parent in self._call_parents:
            parents.setdefault(call_id, deque()).append(parent)
        drafts = {
            item.message_id: "".join(item.text) for item in self._items if isinstance(item, _Draft)
        }
        texts = _by_position(
            [(item.message_id, "".join(item.text)) for item in self._items if _is_text_draft(item)],
            [message.content for message in messages if _is_text_answer(message)],
            _same_text,
        )
        reasonings = _by_position(
            [(message_id, "".join(deltas)) for message_id, deltas in self._reasoning.items()],
            [message.content for message in messages if isinstance(message, ReasoningMessage)],
            _same_text,
        )
        activities = self._activities_by_type(messages)

        claimed: set[str] = set()
        out: list[Message] = []
        for message in messages:
            if isinstance(message, ToolMessage):
                streamed = _take(results, message.tool_call_id)
            elif isinstance(message, AssistantMessage):
                streamed = (
                    _parent_holding(parents, message.tool_calls, drafts, message.content)
                    if message.tool_calls
                    else next(texts, None)
                )
            elif isinstance(message, ReasoningMessage):
                streamed = next(reasonings, None)
            elif isinstance(message, ActivityMessage):
                streamed = next(activities[message.activity_type], None)
            else:
                streamed = None
            if streamed is None or streamed in claimed:
                out.append(message)
                continue
            claimed.add(streamed)
            out.append(message.model_copy(update={"id": streamed}))
        return out

    def _snapshot(self, event: ActivitySnapshotEvent) -> None:
        """Record an ``ACTIVITY_SNAPSHOT``, the way the client applies one.

        A second snapshot for an id keeps the first one's place and takes its
        content, unless it says ``replace=False``, which asks the client to keep
        what it has (``threads_view._with_activities`` keeps place and content
        the same way). Without the ``replace`` check
        ``test_a_snapshot_that_does_not_replace_keeps_the_first_content`` fails.
        """
        known = self._activities.get(event.message_id)
        if known is None:
            self._activities[event.message_id] = _Activity(event.activity_type, event.content)
        elif event.replace is not False:
            known.content = event.content

    def _activities_by_type(self, messages: Sequence[Message]) -> dict[str, Iterator[str]]:
        """The streamed activity ids to lend, per type ``messages`` dumps an activity of.

        Per type, because which activities stream and which dump differ by type:
        ``inject_compaction_events``' ``compaction``, a chart and suggestions
        stream and are never dumped, while a ``pydantic_ai_file`` is dumped and
        never streamed. Pairing all activities as one kind would fail the count
        whenever one of those is in the run.
        """
        dumped: dict[str, list[dict[str, Any]]] = {}
        for message in messages:
            if isinstance(message, ActivityMessage):
                dumped.setdefault(message.activity_type, []).append(message.content)
        return {
            activity_type: _by_position(
                [
                    (message_id, activity.content)
                    for message_id, activity in self._activities.items()
                    if activity.activity_type == activity_type
                ],
                contents,
                _same_content,
            )
            for activity_type, contents in dumped.items()
        }

    def _draft_for(self, message_id: str) -> _Draft:
        for item in reversed(self._items):
            if isinstance(item, _Draft) and item.message_id == message_id:
                return item
        draft = _Draft(message_id=message_id)
        self._items.append(draft)
        return draft


def _is_text_draft(item: _Draft | ToolMessage) -> TypeGuard[_Draft]:
    """Whether ``item`` is a streamed assistant message that carried no tool call.

    Read off the drafts rather than ``messages()``, which skips an empty one: a
    text part with no content is still announced, and still dumped.
    """
    if isinstance(item, ToolMessage):
        return False
    return not item.tool_calls


def _is_text_answer(message: Message) -> TypeGuard[AssistantMessage]:
    """Whether ``message`` is a dumped assistant message that carries no tool call."""
    if not isinstance(message, AssistantMessage):
        return False
    return not message.tool_calls


def _by_position(
    streamed: list[tuple[str, _S]], dumped: list[_D], same: Callable[[_S, _D], bool]
) -> Iterator[str]:
    """The ids of ``streamed`` in order, when it pairs one-for-one with ``dumped``.

    ``streamed`` is each announced message's id and content, ``dumped`` each
    dumped message's content, and ``same`` says whether a pair is one message.
    A count that disagrees means some message of the kind was announced and not
    dumped, or dumped and not announced, and nothing says which; pairing the
    rest by position would shift every id after it onto the wrong message. A
    count that agrees can still be two such disagreements cancelling out, which
    is what the content check catches.

    ``test_text_answers_whose_counts_disagree_all_keep_the_dumped_ids`` and
    ``test_activities_whose_counts_disagree_keep_the_dumped_ids`` fail without the
    count check, each on its assertion -- the first pair of each holds the same
    content, so the content check cannot answer; without the content check
    ``test_a_split_answer_and_a_tool_spoken_text_borrow_no_id``,
    ``test_a_split_answer_and_a_joined_answer_borrow_no_id``,
    ``test_reasoning_whose_text_disagrees_keeps_the_dumped_id`` and
    ``test_an_activity_whose_content_disagrees_keeps_the_dumped_id`` fail.
    """
    if len(streamed) != len(dumped):
        return iter(())
    # Not strict, so that the count check above is what answers a disagreement:
    # strict would raise first, and a test of the count would fail on the raise.
    if not all(same(text, content) for (_, text), content in zip(streamed, dumped, strict=False)):
        return iter(())
    return iter([message_id for message_id, _ in streamed])


def _same_text(streamed: str, dumped: str | None) -> bool:
    """Whether a streamed text and a dumped content are one text.

    Every newline is removed from both before comparing: the dump joins the
    text parts of one message with one, while the stream runs them together.
    Without the removal ``test_an_answer_whose_parts_the_dump_joined_still_pairs``
    fails.
    """
    return streamed.replace("\n", "") == (dumped or "").replace("\n", "")


def _same_content(streamed: dict[str, Any] | None, dumped: dict[str, Any]) -> bool:
    """Whether a streamed activity's content and a dumped one's are one content.

    ``None`` is an activity a delta changed, which is no dumped content.

    The dumped content is compared without its ``canonical_system`` key. As of
    pydantic-ai 2.64.0 the dump writes a compaction part with the response's
    ``canonical_system`` beside it (``part_canonical_system``), while the stream's
    ``handle_compaction`` sees only the part and has no response to read it from;
    an ``OpenAIProvider`` response always carries one, so every OpenAI compaction
    differs from its snapshot by that key alone. Any other difference still
    refuses the id. Without the removal
    ``test_a_dumped_canonical_system_is_not_a_difference`` fails.
    """
    return streamed == {key: value for key, value in dumped.items() if key != "canonical_system"}


def _take(queues: dict[str, deque[_T]], key: str) -> _T | None:
    """The next value queued under ``key``, or ``None`` once there is none."""
    queue = queues.get(key)
    if not queue:
        return None
    return queue.popleft()


def _parent_holding(
    parents: dict[str, deque[str | None]],
    calls: list[ToolCall],
    drafts: dict[str, str],
    content: str | None,
) -> str | None:
    """The parent ``calls`` share, when the message it streamed held ``content``.

    A response of text, thinking, then calls streams the text and the calls as
    one message and dumps them as two, so the dumped calls alone are not the
    message the client knew by that id. Without the text check
    ``test_calls_whose_parent_streamed_other_text_keep_the_dumped_id`` and both
    end-to-end ``borrow_no_id`` tests fail.
    """
    parent = _shared_parent(parents, calls)
    if parent is None:
        return None
    if not _same_text(drafts.get(parent, ""), content):
        return None
    return parent


def _shared_parent(parents: dict[str, deque[str | None]], calls: list[ToolCall]) -> str | None:
    """The one message id every call in ``calls`` streamed under, or ``None``.

    ``None`` too when that one parent is itself ``None``: the protocol lets a
    call name no parent, and then the stream announced no id to keep.
    """
    found = {_take(parents, call.id) for call in calls}
    if len(found) != 1:
        return None
    return found.pop()


def _tool_message(event: ToolCallResultEvent) -> ToolMessage:
    """The stored tool message for a result event, carrying its outcome if it had one.

    The outcome is copied off the event rather than decided here: the stream has
    already been through ``OutcomeAGUIAdapter``, which stamped the calls worth
    stamping, and the completed run's dump stamps the same calls the same way.
    Only the outcome is copied, not the rest of the event's ``metadata``, so this
    exit stores what the other two store and a thread does not read differently
    depending on how its run ended.
    """
    message = ToolMessage(
        id=event.message_id,
        role="tool",
        content=event.content,
        tool_call_id=event.tool_call_id,
    )
    outcome = (event.metadata or {}).get(OUTCOME_FIELD)
    if outcome is None:
        return message
    return stamp_outcome(message, outcome)


__all__ = ["RunTranscript"]
