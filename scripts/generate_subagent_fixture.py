"""Write the sub-agent progress wire fixture, from the server that serves it.

    python scripts/generate_subagent_fixture.py tests/fixtures/subagent_progress_stream.json

The fixture is what a client actually receives when a run delegates: the whole
Server-Sent Events stream of one ``DjangoAGUIView`` run, decoded from the same
encoder a browser reads, with the ``ag_ui.subagent`` ``CUSTOM`` events in the
positions and the order they really arrive in.

**Generated rather than written by hand, and that is the point.** A hand-typed
double describing a wire no server writes makes every test that uses it agree
with the bug, which has happened in this family more than once. So the scenario
below drives the real view, the real ``SubAgents`` capability and the real
observer, and ``tests/agent/test_subagent_fixture.py`` regenerates it on every
run and fails on any difference -- the checked-in file cannot drift from the
server without someone noticing.

The models are scripted (``FunctionModel``), so no provider is called and the
run is the same every time. Two fields are still not: see ``_canonicalized``.

The scenario covers every phase of the contract in one run:

1. a delegation to ``researcher`` that calls a tool successfully, calls it again
   and gets a retry back, then answers -- ``started``, ``tool_call``,
   ``tool_result`` both ways, ``finished``;
2. a delegation to ``auditor`` whose model fails -- ``started``, ``failed``,
   plus the ordinary ``TOOL_CALL_RESULT`` that carries the failure to the model,
   which is where a client reads *why* rather than from the progress channel.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

import django
from django.conf import settings

# One fixed instant stamped over every ``timestamp``. The events really do carry
# one -- dropping the field would misdescribe the wire -- but its value is the
# wall clock, so it is replaced rather than recorded.
FROZEN_TIMESTAMP = 1735689600000

SCENARIO = "One run delegating twice: 'researcher' succeeds after a tool retry, 'auditor' fails."


def _configure() -> None:
    """Django settings, inline, so the generator needs nothing but the package."""
    if not settings.configured:
        settings.configure(
            SECRET_KEY="fixture",
            DEBUG=False,
            ALLOWED_HOSTS=["*"],
            INSTALLED_APPS=[],
            DATABASES={},
            MIDDLEWARE=[],
            USE_TZ=True,
        )
        django.setup()


_configure()

from django.test import RequestFactory  # noqa: E402
from django_pydantic_agent.registry.tool_registry import ToolRegistry  # noqa: E402
from pydantic_ai import Agent, ModelRetry  # noqa: E402
from pydantic_ai.exceptions import UnexpectedModelBehavior  # noqa: E402
from pydantic_ai.messages import ModelMessage  # noqa: E402
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel  # noqa: E402
from pydantic_ai.toolsets import FunctionToolset  # noqa: E402
from pydantic_ai_harness.subagents import SubAgent, SubAgents  # noqa: E402

from django_ag_ui.agent.agui_view import DjangoAGUIView  # noqa: E402
from django_ag_ui.agent.subagent_observer import SubAgentObserver  # noqa: E402


async def lookup_docs(query: str) -> str:
    """Look a phrase up in the handbook."""
    if not query:
        raise ModelRetry("Pass the phrase to look up.")
    return f"Three passages mention {query}."


def _scripted(steps: list[Any]) -> Callable[..., AsyncIterator[Any]]:
    """A ``FunctionModel`` stream function that plays ``steps`` in order.

    Scripted by position rather than by inspecting the history: a step count is
    the same on every run, while branching on message shapes drifts the moment
    upstream changes how a retry is represented.
    """
    remaining = list(steps)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        step = remaining.pop(0)
        if isinstance(step, BaseException):
            raise step
        yield step

    return stream


def _call(name: str, args: str, tool_call_id: str) -> dict[int, DeltaToolCall]:
    return {0: DeltaToolCall(name=name, json_args=args, tool_call_id=tool_call_id)}


def _build_view() -> DjangoAGUIView:
    """The endpoint under observation: two sub-agents, one observer, no provider."""
    researcher = Agent(
        FunctionModel(
            stream_function=_scripted(
                [
                    _call("lookup_docs", '{"query": "settlement"}', "sub-1"),
                    _call("lookup_docs", '{"query": ""}', "sub-2"),
                    "Three passages mention settlement.",
                ]
            )
        ),
        name="researcher",
        description="Looks phrases up in the handbook.",
        toolsets=[FunctionToolset([lookup_docs])],
    )
    auditor = Agent(
        FunctionModel(
            stream_function=_scripted([UnexpectedModelBehavior("the auditor model went away")])
        ),
        name="auditor",
        description="Checks a finding against the ledger.",
    )
    parent = FunctionModel(
        stream_function=_scripted(
            [
                _call(
                    "delegate_task",
                    '{"agent_name": "researcher", "task": "Find settlement passages."}',
                    "call-1",
                ),
                _call(
                    "delegate_task",
                    '{"agent_name": "auditor", "task": "Check them against the ledger."}',
                    "call-2",
                ),
                "The handbook mentions settlement three times; the ledger check did not run.",
            ]
        )
    )
    return DjangoAGUIView(
        ToolRegistry(),
        model=parent,
        require_authenticated=False,
        csrf_exempt=True,
        capabilities=[
            SubAgentObserver(
                SubAgents(
                    agents=[SubAgent(researcher), SubAgent(auditor)],
                    # No disk discovery: the fixture must describe this scenario
                    # and not whatever markdown agents the machine happens to have.
                    agent_folders=None,
                )
            )
        ],
    )


def _run_input() -> bytes:
    return json.dumps(
        {
            "threadId": "thread-fixture",
            "runId": "run-fixture",
            "state": {},
            "messages": [
                {
                    "id": "user-1",
                    "role": "user",
                    "content": "Find the settlement passages and check them.",
                }
            ],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


async def _emitted_events() -> list[dict[str, Any]]:
    """Drive one run and decode every event the SSE encoder wrote."""
    view = _build_view()
    request = RequestFactory().post("/agent/", data=_run_input(), content_type="application/json")
    response = await view(request)
    body = ""
    async for chunk in response.streaming_content:
        body += chunk if isinstance(chunk, str) else chunk.decode()
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


def _canonicalized(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the two fields that cannot repeat, and only those.

    ``timestamp`` is the wall clock and ``messageId`` / ``parentMessageId`` are
    freshly generated UUIDs, so a byte-for-byte regeneration check needs both
    settled. Every other value here is what the server wrote -- including each
    ``toolCallId``, which the scripted models fix, and which is what the progress
    events key on.
    """
    ids: dict[str, str] = {}

    def name_for(value: str) -> str:
        return ids.setdefault(value, f"message-{len(ids) + 1}")

    settled: list[dict[str, Any]] = []
    for event in events:
        copy = dict(event)
        if "timestamp" in copy:
            copy["timestamp"] = FROZEN_TIMESTAMP
        for key in ("messageId", "parentMessageId"):
            if key in copy:
                copy[key] = name_for(copy[key])
        settled.append(copy)
    return settled


def generate() -> str:
    """The fixture's exact contents, as text."""
    events = _canonicalized(asyncio.run(_emitted_events()))
    document = {
        "generator": "scripts/generate_subagent_fixture.py",
        "scenario": SCENARIO,
        "canonicalized": ["timestamp", "messageId", "parentMessageId"],
        "events": events,
    }
    return json.dumps(document, indent=2) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <output.json>", file=sys.stderr)
        return 2
    pathlib.Path(argv[1]).write_text(generate(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
