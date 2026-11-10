from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ag_ui.core import BaseEvent, Message, RunAgentInput, ToolCallResultEvent, ToolMessage
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    NativeToolReturnPart,
    OutputToolResultEvent,
    # Deprecated from pydantic-ai 2.60, where a retry became the call's own
    # ``ToolReturnPart``, and still what every release below it emits for one,
    # the declared floor among them. Named until the floor passes 2.60, so
    # every use carries the same suppression.
    RetryPromptPart,  # ty: ignore[deprecated]
    ToolReturnPart,
)
from pydantic_ai.ui import UIEventStream
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream

from django_ag_ui.agent.stamp_outcome import stamp_outcome


class OutcomeAGUIAdapter(AGUIAdapter[Any, Any]):
    """An ``AGUIAdapter`` whose ``TOOL_CALL_RESULT`` events say how the call went."""

    def build_event_stream(self) -> UIEventStream[RunAgentInput, BaseEvent, Any, Any]:
        """Build the outcome-forwarding event stream."""
        return _OutcomeEventStream(
            self.run_input, accept=self.accept, ag_ui_version=self.ag_ui_version
        )

    @classmethod
    def dump_messages(cls, messages: Sequence[ModelMessage], **kwargs: Any) -> list[Message]:
        """``AGUIAdapter.dump_messages``, each tool result stamped as the stream stamps it."""
        outcomes = _outcomes_by_call(messages)
        dumped = super().dump_messages(messages, **kwargs)
        return [_with_stored_outcome(message, outcomes) for message in dumped]


class _OutcomeEventStream(AGUIEventStream[Any, Any]):
    """``AGUIEventStream`` that stamps each tool result with its part's outcome."""

    def handle_function_tool_result(
        self, event: FunctionToolResultEvent
    ) -> AsyncIterator[BaseEvent]:
        return _with_outcome(
            super().handle_function_tool_result(event), _forwardable_outcome(event.part)
        )

    def handle_output_tool_result(self, event: OutputToolResultEvent) -> AsyncIterator[BaseEvent]:
        return _with_outcome(
            super().handle_output_tool_result(event), _forwardable_outcome(event.part)
        )

    def handle_builtin_tool_return(self, part: NativeToolReturnPart) -> AsyncIterator[BaseEvent]:
        return _with_outcome(super().handle_builtin_tool_return(part), _forwardable_outcome(part))


def _forwardable_outcome(
    part: ToolReturnPart | NativeToolReturnPart | RetryPromptPart,  # ty: ignore[deprecated]
) -> str | None:
    """The part's outcome when it is worth putting on the wire, else ``None``."""
    if isinstance(part, RetryPromptPart):  # ty: ignore[deprecated]
        return None
    if part.outcome == "retried":
        return None
    if part.outcome == "success":
        return None
    return part.outcome


def _outcomes_by_call(messages: Sequence[ModelMessage]) -> dict[str, str]:
    """Each function or output tool call's forwardable outcome, keyed by call id."""
    outcomes: dict[str, str] = {}
    for message in messages:
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            outcome = _forwardable_outcome(part)
            if outcome is not None:
                outcomes[part.tool_call_id] = outcome
    return outcomes


def _with_stored_outcome(message: Message, outcomes: Mapping[str, str]) -> Message:
    """``message``, stamped when it is the result of a call ``outcomes`` names."""
    if not isinstance(message, ToolMessage):
        return message
    outcome = outcomes.get(message.tool_call_id)
    if outcome is None:
        return message
    return stamp_outcome(message, outcome)


async def _with_outcome(
    events: AsyncIterator[BaseEvent], outcome: str | None
) -> AsyncIterator[BaseEvent]:
    """Forward ``events``, stamping ``outcome`` onto every tool result among them."""
    async for event in events:
        if outcome is not None and isinstance(event, ToolCallResultEvent):
            event = stamp_outcome(event, outcome)
        yield event


__all__ = ["OutcomeAGUIAdapter"]
