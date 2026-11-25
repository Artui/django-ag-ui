from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

from ag_ui.core import (
    BaseEvent,
    Message,
    RunAgentInput,
    RunErrorEvent,
    ToolCallResultEvent,
    ToolMessage,
)
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    ModelResponse,
    NativeToolReturnPart,
    OutputToolResultEvent,
    # Deprecated from pydantic-ai 2.60, where a retry became the call's own
    # ``ToolReturnPart``, and still what every release below it emits for one,
    # the declared floor among them. Named until the floor passes 2.60, so
    # every use carries the same suppression.
    RetryPromptPart,  # ty: ignore[deprecated]
    TextPart,
    ToolReturnPart,
)
from pydantic_ai.ui import UIEventStream
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream

from django_ag_ui.agent.message_citations import (
    message_citations,
    stamp_citations,
    with_dumped_citations,
)
from django_ag_ui.agent.message_model_id import stamp_model_id, with_dumped_model_id
from django_ag_ui.agent.serving_model_id import ServingModelId
from django_ag_ui.agent.stamp_answered_by import ANSWERED_BY_FIELD, stamp_answered_by
from django_ag_ui.agent.stamp_outcome import stamp_outcome
from django_ag_ui.agent.utils import run_error_code, run_error_message

# The run input carries these; passing them beside it makes upstream warn.
_RUN_IDENTITY = frozenset({"thread_id", "run_id"})


@dataclass
class OutcomeAGUIAdapter(AGUIAdapter[Any, Any]):
    """An ``AGUIAdapter`` whose ``TOOL_CALL_RESULT`` events say how the call went."""

    serving_model: ServingModelId | None = None

    def build_event_stream(self) -> UIEventStream[RunAgentInput, BaseEvent, Any, Any]:
        """Build the outcome-forwarding event stream."""
        upstream = super().build_event_stream()
        return _OutcomeEventStream(
            **{
                field.name: getattr(upstream, field.name)
                for field in fields(upstream)
                if field.init and field.name not in _RUN_IDENTITY
            },
            serving_model=self.serving_model,
        )

    @classmethod
    def dump_messages(cls, messages: Sequence[ModelMessage], **kwargs: Any) -> list[Message]:
        """``AGUIAdapter.dump_messages``, each tool result stamped as the stream stamps it."""
        outcomes = _outcomes_by_call(messages)
        marks = _answered_by_call(messages)
        dumped = super().dump_messages(messages, **kwargs)
        return [_with_stored_marks(message, outcomes, marks) for message in dumped]

    @staticmethod
    def _dump_response_parts(msg: ModelResponse, **kwargs: Any) -> list[Message]:
        """Upstream's dump of one response, assistant messages stamped with citations and model."""
        dumped = with_dumped_citations(msg, AGUIAdapter._dump_response_parts(msg, **kwargs))
        return with_dumped_model_id(msg.model_id, dumped)


@dataclass
class _OutcomeEventStream(AGUIEventStream[Any, Any]):
    """``AGUIEventStream`` stamping tool results with outcomes, text ends with citations, model."""

    serving_model: ServingModelId | None = None
    _text: str = field(default="", init=False)
    _citations: list[dict[str, Any]] = field(default_factory=list, init=False)

    def handle_text_start(
        self, part: TextPart, follows_text: bool = False
    ) -> AsyncIterator[BaseEvent]:
        if not follows_text:
            self._text, self._citations = "", []
        return super().handle_text_start(part, follows_text)

    async def handle_text_end(
        self, part: TextPart, followed_by_text: bool = False
    ) -> AsyncIterator[BaseEvent]:
        at, self._text = len(self._text), self._text + part.content
        self._citations += message_citations(self._text, at, part.citations or [])
        model_id = self.serving_model.model_id if self.serving_model is not None else None
        async for event in super().handle_text_end(part, followed_by_text):
            yield stamp_model_id(stamp_citations(event, self._citations), model_id)

    def handle_function_tool_result(
        self, event: FunctionToolResultEvent
    ) -> AsyncIterator[BaseEvent]:
        return _with_outcome(
            super().handle_function_tool_result(event),
            _forwardable_outcome(event.part),
            _answered_by(event.part),
        )

    def handle_output_tool_result(self, event: OutputToolResultEvent) -> AsyncIterator[BaseEvent]:
        return _with_outcome(
            super().handle_output_tool_result(event),
            _forwardable_outcome(event.part),
            _answered_by(event.part),
        )

    def handle_builtin_tool_return(self, part: NativeToolReturnPart) -> AsyncIterator[BaseEvent]:
        return _with_outcome(
            super().handle_builtin_tool_return(part), _forwardable_outcome(part), None
        )

    async def on_error(self, error: Exception) -> AsyncIterator[BaseEvent]:
        code = run_error_code(error)
        async for event in super().on_error(error):
            yield (
                event.model_copy(
                    update={"code": code, "message": run_error_message(error, event.message)}
                )
                if isinstance(event, RunErrorEvent)
                else event
            )


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


def _answered_by(part: object) -> str | None:
    """The mark a tool result's metadata carries for who answered its approval, if any."""
    metadata = getattr(part, "metadata", None)
    mark = metadata.get(ANSWERED_BY_FIELD) if isinstance(metadata, dict) else None
    return mark if isinstance(mark, str) else None


def _answered_by_call(messages: Sequence[ModelMessage]) -> dict[str, str]:
    """Each tool call's answered-by mark, keyed by call id."""
    marks: dict[str, str] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and (mark := _answered_by(part)) is not None:
                marks[part.tool_call_id] = mark
    return marks


def _with_stored_marks(
    message: Message, outcomes: Mapping[str, str], marks: Mapping[str, str]
) -> Message:
    """``message``, stamped when it is the result of a call ``outcomes`` or ``marks`` names."""
    if not isinstance(message, ToolMessage):
        return message
    outcome = outcomes.get(message.tool_call_id)
    if outcome is not None:
        message = stamp_outcome(message, outcome)
    mark = marks.get(message.tool_call_id)
    return message if mark is None else stamp_answered_by(message, mark)


async def _with_outcome(
    events: AsyncIterator[BaseEvent], outcome: str | None, answered_by: str | None
) -> AsyncIterator[BaseEvent]:
    """Forward ``events``, stamping ``outcome`` and ``answered_by`` onto every tool result."""
    async for event in events:
        if isinstance(event, ToolCallResultEvent):
            if outcome is not None:
                event = stamp_outcome(event, outcome)
            if answered_by is not None:
                event = stamp_answered_by(event, answered_by)
        yield event


__all__ = ["OutcomeAGUIAdapter"]
