from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from django.http import StreamingHttpResponse
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness.sub_agents import SubAgent, SubAgents, TaskControl

from django_ag_ui import SUBAGENT_EVENT_NAME, SubAgentObserver
from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.authed_request_factory import AuthedRequestFactory

_TASK_ID = re.compile(r"Task ([0-9a-f]{32})")

Step = Callable[[list[ModelMessage]], Any]


def _call(name: str, args: dict[str, Any], tool_call_id: str) -> dict[int, DeltaToolCall]:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=tool_call_id)}


def _task_id(messages: list[ModelMessage]) -> str:
    """The task id the first delegate receipt or report named."""
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and (
                    found := _TASK_ID.search(str(part.content))
                ):
                    return found.group(1)
    raise AssertionError("no task id in the history")


def _played(steps: list[Step]) -> Any:
    """A stream function answering each request with the next step, the last one repeating."""
    remaining = list(steps)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        step = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        yield step(messages)

    return stream


def _researcher(*steps: Step, delay: float = 0.0) -> Agent[Any, Any]:
    async def read_handbook(phrase: str) -> str:
        """Look a phrase up in the handbook."""
        await asyncio.sleep(delay)
        return f"Three passages mention {phrase}."

    return Agent(
        FunctionModel(stream_function=_played(list(steps))),
        name="researcher",
        description="Looks phrases up in the handbook.",
        toolsets=[FunctionToolset([read_handbook])],
    )


def _reads_then_answers(delay: float = 0.0) -> Agent[Any, Any]:
    return _researcher(
        lambda m: _call("read_handbook", {"phrase": "settlement"}, "sub-1"),
        lambda m: "Three passages mention settlement.",
        delay=delay,
    )


def _post() -> Any:
    body = json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "Find the passages."}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()
    return AuthedRequestFactory().post("/agent/", data=body, content_type="application/json")


async def _stream(child: Agent[Any, Any], *parent: Step) -> list[dict[str, Any]]:
    """Every event one task-controlled delegating run puts on the wire."""
    view = DjangoAGUIView(
        ToolRegistry(),
        model=FunctionModel(stream_function=_played(list(parent))),
        capabilities=[
            SubAgentObserver(
                SubAgents(agents=[SubAgent(child)], agent_folders=None, task_control=TaskControl())
            )
        ],
    )
    response = await view(_post())
    assert isinstance(response, StreamingHttpResponse)
    body = ""
    async for chunk in response.streaming_content:
        body += chunk if isinstance(chunk, str) else chunk.decode()
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


def _delegate(**extra: Any) -> Step:
    args = {"agent_name": "researcher", "task": "Find them.", **extra}
    return lambda m: _call("delegate_task", args, "call-1")


def _narration(events: list[dict[str, Any]]) -> list[str]:
    """The lifecycle events and the step phases, in wire order."""
    return [
        event["type"] if event["type"].startswith("SUBAGENT_") else event["value"]["phase"]
        for event in events
        if event["type"].startswith("SUBAGENT_") or event.get("name") == SUBAGENT_EVENT_NAME
    ]


def _phase(events: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    return next(
        event["value"]
        for event in events
        if event.get("name") == SUBAGENT_EVENT_NAME and event["value"]["phase"] == phase
    )


async def test_a_background_task_closes_when_it_settles_not_at_its_receipt() -> None:
    events = await _stream(_reads_then_answers(0.05), _delegate(background=True), lambda m: "Done.")

    assert _narration(events) == [
        "SUBAGENT_STARTED",
        "backgrounded",
        "tool_call",
        "tool_result",
        "SUBAGENT_FINISHED",
    ]


async def test_backgrounding_names_the_task_and_why() -> None:
    events = await _stream(_reads_then_answers(), _delegate(background=True), lambda m: "Done.")
    value = _phase(events, "backgrounded")

    assert value["delegationId"] == "call-1"
    assert value["tool"] is None
    assert re.fullmatch("[0-9a-f]{32}", value["task"]["taskId"])
    assert value["task"]["reason"] == "requested"
    assert value["status"] == "researcher: moved to the background"


async def test_a_task_opens_on_the_parents_delegate_call() -> None:
    events = await _stream(_reads_then_answers(), _delegate(), lambda m: "Done.")
    opened = [event for event in events if event["type"] == "SUBAGENT_STARTED"]

    assert [(event["parentToolCallId"], event["name"]) for event in opened] == [
        ("call-1", "researcher")
    ]
    assert _narration(events) == [
        "SUBAGENT_STARTED",
        "tool_call",
        "tool_result",
        "SUBAGENT_FINISHED",
    ]


async def test_steering_a_running_task_is_reported() -> None:
    events = await _stream(
        _reads_then_answers(0.3),
        _delegate(background=True),
        lambda m: _call("message_task", {"task_id": _task_id(m), "message": "Only 2024."}, "c-2"),
        lambda m: "Done.",
    )
    value = _phase(events, "steered")

    assert value["delegationId"] == "call-1"
    assert value["status"] == "researcher: steered"
    assert value["task"]["message"] == "Only 2024."
    assert value["task"]["source"] == "model"
    assert "truncated" not in value["task"]


async def test_a_cancelled_task_closes_as_an_error_with_a_code() -> None:
    events = await _stream(
        _reads_then_answers(5.0),
        _delegate(background=True),
        lambda m: _call("cancel_task", {"task_id": _task_id(m), "force": True}, "c-2"),
        lambda m: "Stopped.",
    )
    closed = [event for event in events if event["type"] == "SUBAGENT_ERROR"]

    assert _phase(events, "cancel_requested")["task"]["forced"] is True
    assert _phase(events, "cancel_requested")["task"]["source"] == "model"
    assert [(event["code"], event["message"]) for event in closed] == [
        ("cancelled", "researcher was cancelled")
    ]


async def test_a_plain_cancellation_request_is_reported_unforced() -> None:
    events = await _stream(
        _reads_then_answers(0.3),
        _delegate(background=True),
        lambda m: _call("cancel_task", {"task_id": _task_id(m)}, "c-2"),
        lambda m: "Stopped.",
    )

    assert _phase(events, "cancel_requested")["task"]["forced"] is False


async def test_a_question_and_its_answer_are_reported() -> None:
    child = _researcher(
        lambda m: _call("ask_parent", {"question": "Which year?"}, "sub-q"),
        lambda m: "2024 it is.",
    )

    def parent(m: list[ModelMessage]) -> Any:
        return _call("answer_task", {"task_id": _task_id(m), "answer": "2024"}, "c-2")

    events = await _stream(child, _delegate(), parent, lambda m: "Done.")
    narration = _narration(events)

    assert narration.index("question") < narration.index("answered")
    assert _phase(events, "question")["status"] == "researcher: asked a question"
    assert _phase(events, "backgrounded")["task"]["reason"] == "question"
    assert _phase(events, "question")["task"]["question"] == "Which year?"
    answered = _phase(events, "answered")["task"]
    assert (answered["answer"], answered["source"]) == ("2024", "model")
    assert answered["questionId"] == _phase(events, "question")["task"]["questionId"]
    assert narration[-1] == "SUBAGENT_FINISHED"


async def test_a_resumed_task_opens_a_fresh_subagent_run() -> None:
    events = await _stream(
        _reads_then_answers(),
        _delegate(),
        lambda m: _call(
            "delegate_task",
            {"agent_name": "researcher", "task": "And 2023?", "resume": _task_id(m)},
            "call-2",
        ),
        lambda m: "Done.",
    )
    lifecycle = [event for event in events if event["type"].startswith("SUBAGENT_")]
    opened = [event["subagentRunId"] for event in lifecycle if event["type"] == "SUBAGENT_STARTED"]

    assert len(opened) == len(set(opened)) == 2
    assert [event["parentToolCallId"] for event in lifecycle if "parentToolCallId" in event] == [
        "call-1",
        "call-2",
    ]


async def test_the_childs_tool_calls_ride_tagged_tool_call_events() -> None:
    events = await _stream(_reads_then_answers(), _delegate(background=True), lambda m: "Done.")
    run_id = next(e["subagentRunId"] for e in events if e["type"] == "SUBAGENT_STARTED")
    tagged = [event for event in events if event.get("subagentRunId") == run_id]

    assert [event["type"] for event in tagged if event["type"].startswith("TOOL_CALL_")] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
