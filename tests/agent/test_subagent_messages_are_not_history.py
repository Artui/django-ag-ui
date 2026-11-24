from __future__ import annotations

from typing import Any

from ag_ui.core import (
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from pydantic_ai import Agent
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.agent.without_subagent_messages import without_subagent_messages
from tests.agent.test_stored_ids_match_the_stream import USER_TURN, _run, _session, _Store

CHILD_CALL: dict[str, Any] = {
    "id": "child-a",
    "role": "assistant",
    "subagentRunId": "child-run",
    "toolCalls": [
        {"id": "child-c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    ],
}
CHILD_RESULT: dict[str, Any] = {
    "id": "child-t",
    "role": "tool",
    "subagentRunId": "child-run",
    "toolCallId": "child-c1",
    "content": "child result",
}


def _quad(run: str | None) -> list[Any]:
    return [
        ToolCallStartEvent(tool_call_id="c1", tool_call_name="f", subagent_run_id=run),
        ToolCallArgsEvent(tool_call_id="c1", delta="{}", subagent_run_id=run),
        ToolCallEndEvent(tool_call_id="c1", subagent_run_id=run),
        ToolCallResultEvent(message_id="t1", tool_call_id="c1", content="ok", subagent_run_id=run),
    ]


def test_a_tagged_tool_round_never_reaches_the_transcript() -> None:
    transcript = RunTranscript()
    for event in _quad("child-run"):
        transcript.add(event)

    assert transcript.messages() == []


def test_an_untagged_tool_round_is_still_recorded() -> None:
    transcript = RunTranscript()
    for event in _quad(None):
        transcript.add(event)

    assert [message.role for message in transcript.messages()] == ["assistant", "tool"]


async def test_a_posted_tagged_round_never_reaches_the_model_or_the_store() -> None:
    seen: list[list[Any]] = []

    async def stream_fn(messages: list, info: Any) -> Any:
        seen.append(messages)
        yield "answer"

    store = _Store()

    await _run(
        _session(
            Agent(FunctionModel(stream_function=stream_fn)),
            store,
            messages=[
                USER_TURN,
                CHILD_CALL,
                CHILD_RESULT,
                {"id": "u2", "role": "user", "content": "go"},
            ],
        )
    )

    parts = [part for message in seen[0] for part in message.parts]
    assert not [p for p in parts if isinstance(p, ToolCallPart | ToolReturnPart)]
    assert "child" not in str(store.saved[0].messages)


def test_input_with_nothing_tagged_is_returned_as_is() -> None:
    class _Input:
        messages: list[Any] = []

    run_input = _Input()

    assert without_subagent_messages(run_input) is run_input
