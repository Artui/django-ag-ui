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
    """A streamed activity: its type, and its content as the client last had it."""

    activity_type: str
    content: dict[str, Any] | None


class RunTranscript:
    """Accumulate a run's AG-UI events into the messages streamed so far."""

    def __init__(self) -> None:
        self._items: list[_Draft | ToolMessage] = []
        self._pending_calls: dict[str, _PendingCall] = {}
        # Kept apart from the drafts, whose keys are not ids the stream announced.
        self._call_parents: list[tuple[str, str | None]] = []
        self._reasoning: dict[str, list[str]] = {}
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
            self._draft_for(pending.parent_message_id or event.tool_call_id).tool_calls.append(
                call,
            )
        elif isinstance(event, ToolCallResultEvent):
            self._items.append(_tool_message(event))
        elif isinstance(event, ReasoningStartEvent):
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
        """The reconstructed messages, in stream order."""
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
        """``messages``, each under the id the stream announced for it."""
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
        """Record an ``ACTIVITY_SNAPSHOT``, the way the client applies one."""
        known = self._activities.get(event.message_id)
        if known is None:
            self._activities[event.message_id] = _Activity(event.activity_type, event.content)
        elif event.replace is not False:
            known.content = event.content

    def _activities_by_type(self, messages: Sequence[Message]) -> dict[str, Iterator[str]]:
        """The streamed activity ids to lend, per type ``messages`` dumps an activity of."""
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
    """Whether ``item`` is a streamed assistant message that carried no tool call."""
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
    """The ids of ``streamed`` in order, when it pairs one-for-one with ``dumped``."""
    if len(streamed) != len(dumped):
        return iter(())
    if not all(same(text, content) for (_, text), content in zip(streamed, dumped, strict=False)):
        return iter(())
    return iter([message_id for message_id, _ in streamed])


def _same_text(streamed: str, dumped: str | None) -> bool:
    """Whether a streamed text and a dumped content are one text."""
    return streamed.replace("\n", "") == (dumped or "").replace("\n", "")


def _same_content(streamed: dict[str, Any] | None, dumped: dict[str, Any]) -> bool:
    """Whether a streamed activity's content and a dumped one's are one content."""
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
    """The parent ``calls`` share, when the message it streamed held ``content``."""
    parent = _shared_parent(parents, calls)
    if parent is None:
        return None
    if not _same_text(drafts.get(parent, ""), content):
        return None
    return parent


def _shared_parent(parents: dict[str, deque[str | None]], calls: list[ToolCall]) -> str | None:
    """The one message id every call in ``calls`` streamed under, or ``None``."""
    found = {_take(parents, call.id) for call in calls}
    if len(found) != 1:
        return None
    return found.pop()


def _tool_message(event: ToolCallResultEvent) -> ToolMessage:
    """The stored tool message for a result event, carrying its outcome if it had one."""
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
