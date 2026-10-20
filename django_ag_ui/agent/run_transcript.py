from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TypeGuard, TypeVar

from ag_ui.core import (
    AssistantMessage,
    BaseEvent,
    FunctionCall,
    Message,
    ReasoningMessage,
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
        self._reasoning_ids: list[str] = []

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
            # message inside, and is still dumped as a reasoning message.
            self._reasoning_ids.append(event.message_id)

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
          among the streamed ones of its kind, only when the two counts agree.
          Content is no check: consecutive text parts stream as one run of
          deltas but dump joined by a newline.

        A message no key reaches keeps its dumped id, and so does one whose
        streamed id an earlier message already took -- a response whose calls a
        thinking part splits in two dumps as two assistant messages under one
        parent, and one stored id naming two messages would be worse than a
        fresh id naming one. Withheld reasoning (``FORWARD_REASONING`` off) is
        the common case of the first: the client was announced no id for it.
        Calls and results are matched in order per id, so a retry that reuses a
        call id pairs off in sequence.

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
        texts = _by_position(
            [item.message_id for item in self._items if _is_text_draft(item)],
            [message for message in messages if _is_text_answer(message)],
        )
        reasonings = _by_position(
            self._reasoning_ids,
            [message for message in messages if isinstance(message, ReasoningMessage)],
        )

        claimed: set[str] = set()
        out: list[Message] = []
        for message in messages:
            if isinstance(message, ToolMessage):
                streamed = _take(results, message.tool_call_id)
            elif isinstance(message, AssistantMessage):
                streamed = (
                    _shared_parent(parents, message.tool_calls)
                    if message.tool_calls
                    else next(texts, None)
                )
            elif isinstance(message, ReasoningMessage):
                streamed = next(reasonings, None)
            else:
                streamed = None
            if streamed is None or streamed in claimed:
                out.append(message)
                continue
            claimed.add(streamed)
            out.append(message.model_copy(update={"id": streamed}))
        return out

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


def _is_text_answer(message: Message) -> bool:
    """Whether ``message`` is a dumped assistant message that carries no tool call."""
    if not isinstance(message, AssistantMessage):
        return False
    return not message.tool_calls


def _by_position(streamed: list[str], dumped: list[Message]) -> Iterator[str]:
    """``streamed`` in order when it pairs one-for-one with ``dumped``, else nothing.

    A count that disagrees means some message of the kind was announced and not
    dumped, or dumped and not announced, and nothing says which; pairing the
    rest by position would shift every id after it onto the wrong message.
    """
    if len(streamed) != len(dumped):
        return iter(())
    return iter(streamed)


def _take(queues: dict[str, deque[_T]], key: str) -> _T | None:
    """The next value queued under ``key``, or ``None`` once there is none."""
    queue = queues.get(key)
    if not queue:
        return None
    return queue.popleft()


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
