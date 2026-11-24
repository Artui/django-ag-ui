from __future__ import annotations

from ag_ui.core import (
    BaseEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent

from django_ag_ui.agent.event_timestamp import event_timestamp
from django_ag_ui.agent.subagent_lifecycle import subagent_run_id


def subagent_tool_calls(
    event: FunctionToolCallEvent | FunctionToolResultEvent, *, delegation_id: str
) -> list[BaseEvent]:
    """The protocol's ``TOOL_CALL_*`` events for one child tool event, tagged with its run."""
    run_id = subagent_run_id(delegation_id)
    timestamp = event_timestamp()
    if isinstance(event, FunctionToolCallEvent):
        part = event.part
        return [
            ToolCallStartEvent(
                tool_call_id=part.tool_call_id,
                tool_call_name=part.tool_name,
                subagent_run_id=run_id,
                timestamp=timestamp,
            ),
            ToolCallArgsEvent(
                tool_call_id=part.tool_call_id,
                delta=part.args if isinstance(part.args, str) else part.args_as_json_str(),
                subagent_run_id=run_id,
                timestamp=timestamp,
            ),
            ToolCallEndEvent(
                tool_call_id=part.tool_call_id, subagent_run_id=run_id, timestamp=timestamp
            ),
        ]
    result = event.part
    return [
        ToolCallResultEvent(
            message_id=f"{run_id}:{result.tool_call_id}",
            tool_call_id=result.tool_call_id,
            content=result.model_response_str(),
            role="tool",
            subagent_run_id=run_id,
            timestamp=timestamp,
        )
    ]


__all__ = ["subagent_tool_calls"]
