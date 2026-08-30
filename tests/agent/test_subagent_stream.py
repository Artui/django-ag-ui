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


async def _progress() -> list[dict[str, Any]]:
    response = await _view()(_post())
    assert isinstance(response, StreamingHttpResponse)
    return [
        event["value"]
        for event in await _events(response)
        if event["type"] == "CUSTOM" and event["name"] == SUBAGENT_EVENT_NAME
    ]


async def test_a_delegation_reports_itself_from_start_to_finish() -> None:
    assert [value["phase"] for value in await _progress()] == [
        "started",
        "tool_call",
        "tool_result",
        "finished",
    ]


async def test_every_event_is_keyed_to_the_parents_own_tool_call() -> None:
    # The client already drew a card for ``call-1`` off TOOL_CALL_START. Keying
    # on that id is what makes this an augmentation of that card rather than a
    # second row beside it.
    assert {value["delegationId"] for value in await _progress()} == {"call-1"}


async def test_the_childs_tool_call_is_named_with_its_outcome() -> None:
    tools = [value["tool"] for value in await _progress() if "tool" in value]

    assert tools == [
        {"toolCallId": "sub-1", "name": "read_handbook", "ok": None},
        {"toolCallId": "sub-1", "name": "read_handbook", "ok": True},
    ]


async def test_the_progress_arrives_around_the_delegate_tool_call() -> None:
    # Ordering on the wire, not just presence: the first progress event has to
    # follow the tool call it belongs to, and the last has to precede its result.
    response = await _view()(_post())
    types = [(event["type"], event.get("name")) for event in await _events(response)]
    first = types.index(("CUSTOM", SUBAGENT_EVENT_NAME))
    last = len(types) - 1 - types[::-1].index(("CUSTOM", SUBAGENT_EVENT_NAME))

    assert types.index(("TOOL_CALL_END", None)) < first
    assert last < types.index(("TOOL_CALL_RESULT", None))


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
    progress = [
        event["value"]
        for event in await _events(response)
        if event.get("name") == SUBAGENT_EVENT_NAME
    ]

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

    assert not [
        event for event in await _events(response) if event.get("name") == SUBAGENT_EVENT_NAME
    ]
