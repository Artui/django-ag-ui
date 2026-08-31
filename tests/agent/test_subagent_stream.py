"""A delegation seen from the wire: the whole composition, end to end.

The three parts are tested apart -- the event's shape, the observer's noticing,
the injector's timing -- and none of them proves the endpoint is wired. This
drives the real view with a real ``SubAgents`` capability and reads what a
browser would read.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from django.http import StreamingHttpResponse
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness.subagents import SubAgent, SubAgents

from django_ag_ui import SUBAGENT_EVENT_NAME, SubAgentObserver
from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.authed_request_factory import AuthedRequestFactory


async def read_handbook(phrase: str) -> str:
    """Look a phrase up in the handbook."""
    return f"Three passages mention {phrase}."


def _scripted(steps: list[Any]) -> Any:
    """A ``FunctionModel`` stream function that plays ``steps`` in order."""
    remaining = list(steps)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        yield remaining.pop(0)

    return stream


def _call(name: str, args: str, tool_call_id: str) -> dict[int, DeltaToolCall]:
    return {0: DeltaToolCall(name=name, json_args=args, tool_call_id=tool_call_id)}


def _view() -> DjangoAGUIView:
    researcher = Agent(
        FunctionModel(
            stream_function=_scripted(
                [
                    _call("read_handbook", '{"phrase": "settlement"}', "sub-1"),
                    "Three passages mention settlement.",
                ]
            )
        ),
        name="researcher",
        description="Looks phrases up in the handbook.",
        toolsets=[FunctionToolset([read_handbook])],
    )
    parent = FunctionModel(
        stream_function=_scripted(
            [
                _call(
                    "delegate_task",
                    '{"agent_name": "researcher", "task": "Find them."}',
                    "call-1",
                ),
                "Three, as it turns out.",
            ]
        )
    )
    return DjangoAGUIView(
        ToolRegistry(),
        model=parent,
        capabilities=[
            SubAgentObserver(SubAgents(agents=[SubAgent(researcher)], agent_folders=None))
        ],
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


async def _events(response: StreamingHttpResponse) -> list[dict[str, Any]]:
    body = ""
    async for chunk in response.streaming_content:
        body += chunk if isinstance(chunk, str) else chunk.decode()
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


async def _stream() -> list[dict[str, Any]]:
    """Every event one delegating run puts on the wire, in the order it sent them."""
    response = await _view()(_post())
    assert isinstance(response, StreamingHttpResponse)
    return await _events(response)


def _is_step(event: dict[str, Any]) -> bool:
    """A ``CUSTOM`` step: one tool call the child made."""
    return event["type"] == "CUSTOM" and event.get("name") == SUBAGENT_EVENT_NAME


def _is_lifecycle(event: dict[str, Any]) -> bool:
    """One of the protocol's own three, which open and close the delegation."""
    return str(event["type"]).startswith("SUBAGENT_")


async def _progress() -> list[dict[str, Any]]:
    return [event["value"] for event in await _stream() if _is_step(event)]


async def test_a_delegation_reports_itself_from_start_to_finish() -> None:
    # The claim the split has to earn: two carriers, one narrative, in order.
    # A client reading only the protocol's events sees the delegation open and
    # close; one that also reads the CUSTOM channel sees what happened between.
    narration = [
        event["type"] if _is_lifecycle(event) else event["value"]["phase"]
        for event in await _stream()
        if _is_lifecycle(event) or _is_step(event)
    ]

    assert narration == [
        "SUBAGENT_STARTED",
        "tool_call",
        "tool_result",
        "SUBAGENT_FINISHED",
    ]


async def test_the_delegation_opens_and_closes_on_one_run_id() -> None:
    # The protocol refuses a close that names no open delegation, and refuses
    # RUN_FINISHED while one is still open -- so this pair being consistent is
    # what keeps the run acceptable to the client, not a cosmetic nicety.
    opened, closed = [event for event in await _stream() if _is_lifecycle(event)]

    assert opened["subagentRunId"] == closed["subagentRunId"]


async def test_every_event_is_keyed_to_the_parents_own_tool_call() -> None:
    # The client already drew a card for ``call-1`` off TOOL_CALL_START. Keying
    # on that id is what makes this an augmentation of that card rather than a
    # second row beside it -- and both carriers have to agree on it, since the
    # client joins them by it.
    events = await _stream()
    opened = next(event for event in events if event["type"] == "SUBAGENT_STARTED")

    assert opened["parentToolCallId"] == "call-1"
    assert {event["value"]["delegationId"] for event in events if _is_step(event)} == {"call-1"}


async def test_the_childs_tool_call_is_named_with_its_outcome() -> None:
    tools = [value["tool"] for value in await _progress() if "tool" in value]

    assert tools == [
        {"toolCallId": "sub-1", "name": "read_handbook", "ok": None},
        {"toolCallId": "sub-1", "name": "read_handbook", "ok": True},
    ]


async def test_the_progress_arrives_around_the_delegate_tool_call() -> None:
    # Ordering on the wire, not just presence: the delegation has to open after
    # the tool call it belongs to, and close before that call's result.
    types = [event["type"] for event in await _stream()]

    assert types.index("TOOL_CALL_END") < types.index("SUBAGENT_STARTED")
    assert types.index("SUBAGENT_FINISHED") < types.index("TOOL_CALL_RESULT")


async def test_two_delegations_at_once_do_not_cross_talk() -> None:
    # The client groups by delegationId, so parallel delegations have to stay
    # apart on the wire. They can, because pydantic-ai runs concurrent tool
    # calls in their own tasks and the correlation is a context variable set
    # inside each -- each write lands in that task's own copy of the context.
    def child(name: str, tool_call_id: str, delay: float) -> Agent[Any, Any]:
        async def work(query: str) -> str:
            """Take a while."""
            await asyncio.sleep(delay)
            return "done"

        toolset: FunctionToolset[Any] = FunctionToolset()
        toolset.add_function(work, name=f"work_{name}")
        return Agent(
            FunctionModel(
                stream_function=_scripted(
                    [
                        _call(f"work_{name}", '{"query": "x"}', tool_call_id),
                        f"{name} is done",
                    ]
                )
            ),
            name=name,
            description=f"The {name} sub-agent.",
            toolsets=[toolset],
        )

    view = DjangoAGUIView(
        ToolRegistry(),
        model=FunctionModel(
            stream_function=_scripted(
                [
                    {
                        0: DeltaToolCall(
                            name="delegate_task",
                            json_args='{"agent_name": "alpha", "task": "a"}',
                            tool_call_id="call-a",
                        ),
                        1: DeltaToolCall(
                            name="delegate_task",
                            json_args='{"agent_name": "beta", "task": "b"}',
                            tool_call_id="call-b",
                        ),
                    },
                    "Both are done.",
                ]
            )
        ),
        capabilities=[
            SubAgentObserver(
                SubAgents(
                    # beta finishes first, so the two are genuinely interleaved
                    # rather than merely started together.
                    agents=[
                        SubAgent(child("alpha", "sub-a", 0.05)),
                        SubAgent(child("beta", "sub-b", 0.0)),
                    ],
                    agent_folders=None,
                )
            )
        ],
    )
    response = await view(_post())
    events = await _events(response)
    progress = [event["value"] for event in events if event.get("name") == SUBAGENT_EVENT_NAME]

    # The lifecycle half, which is where the protocol is strictest: it refuses a
    # reused subagentRunId inside one run, and refuses RUN_FINISHED while any
    # delegation is still open. Two parallel delegations are the case that would
    # break both if the correlation leaked between tasks.
    lifecycle = [event for event in events if _is_lifecycle(event)]
    assert {
        event["subagentRunId"] for event in lifecycle if event["type"] == "SUBAGENT_STARTED"
    } == {event["subagentRunId"] for event in lifecycle if event["type"] == "SUBAGENT_FINISHED"}
    assert len(lifecycle) == 4
    assert {
        event["parentToolCallId"] for event in lifecycle if event["type"] == "SUBAGENT_STARTED"
    } == {"call-a", "call-b"}

    by_delegation: dict[str, list[str]] = {"call-a": [], "call-b": []}
    for value in progress:
        by_delegation[value["delegationId"]].append(value["agent"])
    assert set(by_delegation["call-a"]) == {"alpha"}
    assert set(by_delegation["call-b"]) == {"beta"}
    # Interleaved on the wire, which is what a client has to cope with.
    assert [value["delegationId"] for value in progress] != sorted(
        value["delegationId"] for value in progress
    )


async def test_an_unwrapped_capability_emits_nothing() -> None:
    # Opt-in by construction: the wrapping is the switch, and there is no
    # setting that turns this on behind it.
    researcher = Agent(
        FunctionModel(stream_function=_scripted(["nothing to report"])),
        name="researcher",
        description="Looks phrases up in the handbook.",
    )
    view = DjangoAGUIView(
        ToolRegistry(),
        model=FunctionModel(
            stream_function=_scripted(
                [
                    _call(
                        "delegate_task",
                        '{"agent_name": "researcher", "task": "Find them."}',
                        "call-1",
                    ),
                    "Nothing found.",
                ]
            )
        ),
        capabilities=[SubAgents(agents=[SubAgent(researcher)], agent_folders=None)],
    )
    response = await view(_post())

    # Neither carrier, not just the CUSTOM one: the wrapping is the switch, and
    # adopting the protocol's own events did not add a second way in.
    events = await _events(response)
    assert not [event for event in events if _is_step(event) or _is_lifecycle(event)]
