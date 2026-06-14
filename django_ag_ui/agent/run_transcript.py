from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ag_ui.core import (
    AssistantMessage,
    BaseEvent,
    FunctionCall,
    Message,
    TextMessageContentEvent,
    TextMessageStartEvent,
    ToolCall,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    ToolMessage,
)


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
    """

    def __init__(self) -> None:
        self._items: list[_Draft | ToolMessage] = []
        self._pending_calls: dict[str, _PendingCall] = {}

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
            self._items.append(
                ToolMessage(
                    id=event.message_id,
                    role="tool",
                    content=event.content,
                    tool_call_id=event.tool_call_id,
                ),
            )

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

    def _draft_for(self, message_id: str) -> _Draft:
        for item in reversed(self._items):
            if isinstance(item, _Draft) and item.message_id == message_id:
                return item
        draft = _Draft(message_id=message_id)
        self._items.append(draft)
        return draft


__all__ = ["RunTranscript"]
