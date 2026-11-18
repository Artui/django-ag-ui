from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django_spec_agents import DESTRUCTIVE_METADATA_KEY
from django_spec_agents.child_sessions.types.inbound_kind import InboundKind
from django_spec_agents.child_sessions.wake_thread import wake_thread
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.contrib.store.models import StoredThreadRun
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_waiting import RunWaiting
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.policy.guard.types.tool_guard_config import ToolGuardConfig
from pydantic_ai import Tool
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from django_ag_ui import ToolRegistry, tool
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.constants import RUN_LEASE
from tests.authed_request_factory import AuthedAsyncRequestFactory, authenticated_user

_DELETE_ARGS = '{"target": "widget-1"}'


class _Calls:
    """Calls ``name`` once, then answers ``done`` once a tool has returned."""

    def __init__(self, name: str, args: str = "{}") -> None:
        self.name, self.args = name, args
        self.ran: list[str] = []

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            parts = [p for m in messages for p in getattr(m, "parts", [])]
            if any(getattr(p, "part_kind", "") == "tool-return" for p in parts):
                yield "done"
            else:
                yield {0: DeltaToolCall(name=self.name, json_args=self.args, tool_call_id="call-1")}

        return FunctionModel(stream_function=stream_fn)


def _guarded(calls: _Calls, **config: Any) -> dict[str, Any]:
    registry = ToolRegistry()

    @tool(registry, destructive=True)
    def delete_thing(target: str) -> str:
        """Delete a thing."""
        calls.ran.append(target)
        return f"deleted {target}"

    @tool(registry)
    def ask(topic: str) -> str:
        """Ask the person a question."""
        raise CallDeferred(kind="question")

    return {
        "registry": registry,
        "model": calls.model(),
        "csrf_exempt": True,
        "conversation_store": DefaultConversationStore(),
        "step_store": DefaultStepStore,
        "run_store": DefaultRunStore(),
        "config": build_ag_ui_config(
            heartbeat_seconds=0, **{"tool_guard": ToolGuardConfig(enabled=True), **config}
        ),
    }


def _server(options: dict[str, Any]) -> AGUIServer:
    registry = options.pop("registry")
    return AGUIServer(registry, id="helper", **options)


async def _parked(server: AGUIServer, prompt: str = "go") -> Any:
    user = authenticated_user()
    stop = await AGUIHeadlessRunner(server).run_to_first_stop("helper", prompt, user, timeout=5)
    state = await DefaultRunStore().get(stop.thread_id, request=server.headless_request(user))
    return stop, state


@pytest.mark.django_db(transaction=True)
async def test_a_gated_headless_run_parks_on_the_call_its_interrupt_names() -> None:
    server = _server(_guarded(_Calls("delete_thing", _DELETE_ARGS)))

    stop, state = await _parked(server)

    assert stop.outcome is RunOutcome.WAITING
    assert (state.status, state.active_run_id) == (ThreadRunStatus.WAITING, None)
    assert state.waiting == RunWaiting(
        kind="approval", summary="delete_thing", tool_call_id="call-1", destructive=True
    )
    request = server.headless_request(authenticated_user())
    finished = (await DefaultRunStore().read(stop.run_id, request=request))[-1].event
    [interrupt] = finished["outcome"]["interrupts"]
    assert interrupt["id"] == f"int-{state.waiting.tool_call_id}"
    assert stop.waiting == state.waiting


@pytest.mark.django_db(transaction=True)
async def test_a_parked_approval_carries_its_configured_prompt() -> None:
    options = _guarded(
        _Calls("delete_thing", _DELETE_ARGS), approval_prompts={"delete_thing": "Delete it?"}
    )

    _, state = await _parked(_server(options))

    assert state.waiting.summary == "Delete it?"


class _CallsBoth(_Calls):
    """Asks, then calls a gated tool, in one response."""

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            yield {
                0: DeltaToolCall(name="ask", json_args='{"topic": "x"}', tool_call_id="call-0"),
                1: DeltaToolCall(
                    name="delete_thing", json_args=_DELETE_ARGS, tool_call_id="call-1"
                ),
            }

        return FunctionModel(stream_function=stream_fn)


@pytest.mark.django_db(transaction=True)
async def test_a_run_deferring_several_calls_parks_on_its_approval_first() -> None:
    _, state = await _parked(_server(_guarded(_CallsBoth("ask"))))

    assert (state.waiting.kind, state.waiting.tool_call_id) == ("approval", "call-1")


@pytest.mark.django_db(transaction=True)
async def test_a_question_parks_as_a_question_that_is_not_destructive() -> None:
    _, state = await _parked(_server(_guarded(_Calls("ask", '{"topic": "colour"}'))))

    assert (state.waiting.kind, state.waiting.destructive) == ("question", False)
    assert state.waiting.summary == "ask"


@pytest.mark.django_db(transaction=True)
async def test_destructive_is_read_from_the_tools_own_definition() -> None:
    calls = _Calls("purge")

    def purge() -> str:
        return "purged"

    toolset = FunctionToolset()
    toolset.add_tool(Tool(purge, requires_approval=True, metadata={DESTRUCTIVE_METADATA_KEY: True}))
    options = _guarded(calls, tool_guard=ToolGuardConfig())
    options["toolsets"] = [toolset]

    _, state = await _parked(_server(options))

    assert (state.waiting.kind, state.waiting.destructive) == ("approval", True)


@pytest.mark.django_db(transaction=True)
async def test_an_approval_the_guard_does_not_ask_for_is_not_destructive() -> None:
    def peek() -> str:
        return "peeked"

    toolset = FunctionToolset()
    toolset.add_tool(Tool(peek, requires_approval=True))
    options = _guarded(_Calls("peek"), tool_guard=ToolGuardConfig())
    options["toolsets"] = [toolset]

    _, state = await _parked(_server(options))

    assert (state.waiting.kind, state.waiting.destructive) == ("approval", False)


_PICK = {
    "name": "pick_colour",
    "description": "Let the person pick a colour.",
    "parameters": {"type": "object", "properties": {}},
}
_ASKED_TO_DELETE = [
    {"id": "u1", "role": "user", "content": "delete widget-1"},
    {
        "id": "a1",
        "role": "assistant",
        "toolCalls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "delete_thing", "arguments": _DELETE_ARGS},
            }
        ],
    },
]


def _browser_body(
    run_id: str,
    *,
    detach: bool = False,
    tools: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
    resume: list[dict[str, Any]] | None = None,
) -> bytes:
    body: dict[str, Any] = {
        "threadId": "t-browser",
        "runId": run_id,
        "messages": messages or [{"id": "u1", "role": "user", "content": "go"}],
        "tools": tools or [],
        "context": [],
        "state": None,
        "forwardedProps": {"detach": True} if detach else {},
    }
    if resume is not None:
        body["resume"] = resume
    return json.dumps(body).encode()


async def _settled(thread_id: str, request: Any) -> Any:
    for _ in range(300):
        state = await DefaultRunStore().get(thread_id, request=request)
        if state is not None and state.status is not ThreadRunStatus.RUNNING:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("the run never settled")


async def _browser_run(view: DjangoAGUIView, body: bytes) -> Any:
    request = AuthedAsyncRequestFactory().post(
        "/agent/", data=body, content_type="application/json"
    )
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    async for _ in response:  # ty: ignore[not-iterable]
        pass
    return await _settled("t-browser", request)


def _browser_view(calls: _Calls) -> DjangoAGUIView:
    options = _guarded(calls, run_timeout_seconds=300)
    return DjangoAGUIView(options.pop("registry"), **options)


@pytest.mark.django_db(transaction=True)
async def test_a_detached_browser_run_parks_on_a_client_tool_as_external() -> None:
    body = _browser_body("r1", detach=True, tools=[_PICK])

    state = await _browser_run(_browser_view(_Calls("pick_colour")), body)

    assert state.status is ThreadRunStatus.WAITING
    assert state.waiting == RunWaiting(
        kind="external", summary="pick_colour", tool_call_id="call-1", destructive=False
    )


@pytest.mark.django_db(transaction=True)
async def test_a_client_tool_stamped_destructive_parks_as_destructive() -> None:
    stamped = {**_PICK, "parameters": {**_PICK["parameters"], "x-destructive": True}}
    body = _browser_body("r1", detach=True, tools=[stamped])

    state = await _browser_run(_browser_view(_Calls("pick_colour")), body)

    assert (state.waiting.kind, state.waiting.destructive) == ("external", True)


@pytest.mark.django_db(transaction=True)
async def test_a_connected_browser_run_ending_on_a_client_tool_does_not_park() -> None:
    body = _browser_body("r1", tools=[_PICK])

    state = await _browser_run(_browser_view(_Calls("pick_colour")), body)

    assert (state.status, state.waiting) == (ThreadRunStatus.IDLE, None)


@pytest.mark.django_db(transaction=True)
async def test_a_connected_browser_approval_parks_then_its_answer_completes() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    view = _browser_view(calls)
    parked = await _browser_run(view, _browser_body("r1"))
    answer = [{"interruptId": "int-call-1", "status": "resolved", "payload": {"approved": True}}]

    state = await _browser_run(view, _browser_body("r2", messages=_ASKED_TO_DELETE, resume=answer))

    assert (parked.status, parked.waiting.kind) == (ThreadRunStatus.WAITING, "approval")
    assert (state.status, state.waiting, state.last_run_id) == (ThreadRunStatus.IDLE, None, "r2")
    assert calls.ran == ["widget-1"]


@pytest.mark.django_db(transaction=True)
async def test_an_answered_question_resumes_a_parked_child_through_wake_thread() -> None:
    queue = DefaultInboundQueue()
    options = _guarded(_Calls("ask", '{"topic": "colour"}'))
    server = AGUIServer(options.pop("registry"), id="helper", inbound_queue=queue, **options)
    runner = AGUIHeadlessRunner(server)
    user = authenticated_user()
    request = server.headless_request(user)
    parent = ThreadRef("t-parent", "planner")
    lineage = ChildLineageRecord(root=parent, depth=1, tools=["ask"], gated=[])
    await runner.run_to_first_stop(
        "helper", "go", user, timeout=5, thread_id="t-child", parent=parent, lineage=lineage
    )
    child = ThreadRef("t-child", "helper")
    answer = {"text": "blue"}
    await queue.enqueue(child, InboundKind.ANSWER, answer, request=request, sender=parent)

    started = await wake_thread(
        child,
        request=request,
        runner=runner,
        runs=DefaultRunStore(),
        queue=queue,
        timeout=5,
        conversations=DefaultConversationStore(),
    )

    assert started is not None
    state = await _settled("t-child", request)
    assert (state.status, state.last_run_id) == (ThreadRunStatus.IDLE, started.run_id)
    stored = await DefaultConversationStore().load("t-child", request=request)
    assert [m.get("content") for m in stored.messages][-2:] == ["blue", "done"]


_APPROVED = [{"interruptId": "int-call-1", "status": "resolved", "payload": {"approved": True}}]


async def _resume(options: dict[str, Any], stop: Any, run_id: str) -> Any:
    view = DjangoAGUIView(
        options["registry"], **{k: v for k, v in options.items() if k != "registry"}
    )
    body = {
        "threadId": stop.thread_id,
        "runId": run_id,
        "messages": [],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
        "resume": _APPROVED,
    }
    request = AuthedAsyncRequestFactory().post(
        f"/agent/resume/{stop.run_id}/", data=json.dumps(body), content_type="application/json"
    )
    response = await view(request, resume_from=stop.run_id)
    if isinstance(response, StreamingHttpResponse):
        chunks = [c if isinstance(c, str) else c.decode() async for c in response]  # ty: ignore[not-iterable]
        return "".join(chunks), await _settled(stop.thread_id, request)
    return response, None


@pytest.mark.django_db(transaction=True)
async def test_answering_a_parked_run_through_its_resume_route_completes_it() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls)
    stop, _ = await _parked(_server(dict(options)))

    streamed, state = await _resume(options, stop, "r-answer")

    assert "deleted widget-1" in streamed
    assert calls.ran == ["widget-1"]
    assert (state.status, state.waiting, state.last_run_id) == (
        ThreadRunStatus.IDLE,
        None,
        "r-answer",
    )


@pytest.mark.django_db(transaction=True)
async def test_a_parked_run_already_answered_is_not_resumable_again() -> None:
    options = _guarded(_Calls("delete_thing", _DELETE_ARGS))
    stop, _ = await _parked(_server(dict(options)))
    await _resume(options, stop, "r-answer")

    response, _ = await _resume(options, stop, "r-again")

    assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_a_run_its_thread_has_parked_past_is_not_resumable() -> None:
    options = _guarded(_Calls("delete_thing", _DELETE_ARGS))
    stop, state = await _parked(_server(dict(options)))
    runs, request = DefaultRunStore(), _server(dict(options)).headless_request(authenticated_user())
    await runs.claim(stop.thread_id, "r-later", request=request, lease=RUN_LEASE)
    await runs.finish(
        stop.thread_id, "r-later", RunOutcome.WAITING, request=request, waiting=state.waiting
    )

    response, _ = await _resume(options, stop, "r-answer")

    assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_a_parked_run_whose_thread_no_longer_waits_is_not_resumable() -> None:
    options = _guarded(_Calls("delete_thing", _DELETE_ARGS))
    stop, _ = await _parked(_server(dict(options)))
    await StoredThreadRun.objects.filter(thread_id=stop.thread_id).aupdate(
        status=ThreadRunStatus.IDLE, waiting=None
    )

    response, _ = await _resume(options, stop, "r-answer")

    assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_a_run_parks_without_the_harness_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    options = _guarded(_Calls("delete_thing", _DELETE_ARGS))
    del options["step_store"]
    monkeypatch.setitem(sys.modules, "pydantic_ai_harness.step_persistence", None)

    state = await _browser_run(
        DjangoAGUIView(options.pop("registry"), **options), _browser_body("r1")
    )

    assert (state.status, state.waiting.kind) == (ThreadRunStatus.WAITING, "approval")
