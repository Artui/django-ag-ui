from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django_service_specs import NotPermitted
from django_spec_agents import DESTRUCTIVE_METADATA_KEY, RunNotParkedError
from django_spec_agents.child_sessions.types.inbound_kind import InboundKind
from django_spec_agents.child_sessions.wake_thread import wake_thread
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.contrib.store.models import StoredThreadRun
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
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
from django_ag_ui.agent.partial_answer_error import PartialAnswerError
from django_ag_ui.agent.thread_busy_error import ThreadBusyError
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.constants import RUN_LEASE
from tests.agent.worker_queue import WorkerQueue
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
    assert state.waiting == (
        RunWaiting(
            kind="approval", summary="delete_thing", tool_call_id="call-1", destructive=True
        ),
    )
    request = server.headless_request(authenticated_user())
    finished = (await DefaultRunStore().read(stop.run_id, request=request))[-1].event
    [interrupt] = finished["outcome"]["interrupts"]
    assert interrupt["id"] == f"int-{state.waiting[0].tool_call_id}"
    assert stop.waiting == state.waiting


@pytest.mark.django_db(transaction=True)
async def test_a_parked_approval_carries_its_configured_prompt() -> None:
    options = _guarded(
        _Calls("delete_thing", _DELETE_ARGS), approval_prompts={"delete_thing": "Delete it?"}
    )

    _, state = await _parked(_server(options))

    assert state.waiting[0].summary == "Delete it?"


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
async def test_a_run_deferring_several_calls_parks_on_every_call_approvals_first() -> None:
    _, state = await _parked(_server(_guarded(_CallsBoth("ask"))))

    assert [(w.kind, w.tool_call_id) for w in state.waiting] == [
        ("approval", "call-1"),
        ("question", "call-0"),
    ]


class _DeletesTwice(_Calls):
    """Calls the gated tool twice in one response."""

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            yield {
                i: DeltaToolCall(name="delete_thing", json_args=args, tool_call_id=f"call-{i}")
                for i, args in enumerate(('{"target": "a"}', '{"target": "b"}'))
            }

        return FunctionModel(stream_function=stream_fn)


@pytest.mark.django_db(transaction=True)
async def test_a_thread_parked_on_two_approvals_reads_back_both_interrupts() -> None:
    server = _server(_guarded(_DeletesTwice("delete_thing")))
    stop, _ = await _parked(server)
    patterns, _, _ = server.urls
    threads = next(p for p in patterns if p.name == "threads").callback

    response = await threads(AuthedAsyncRequestFactory().get("/agent/threads/"))

    [row] = [r for r in json.loads(response.content)["threads"] if r["thread_id"] == stop.thread_id]
    assert [i["toolCallId"] for i in row["interrupts"]] == ["call-0", "call-1"]


@pytest.mark.django_db(transaction=True)
async def test_a_question_parks_as_a_question_that_is_not_destructive() -> None:
    _, state = await _parked(_server(_guarded(_Calls("ask", '{"topic": "colour"}'))))

    [waiting] = state.waiting
    assert (waiting.kind, waiting.destructive, waiting.summary) == ("question", False, "ask")


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

    assert [(w.kind, w.destructive) for w in state.waiting] == [("approval", True)]


@pytest.mark.django_db(transaction=True)
async def test_an_approval_the_guard_does_not_ask_for_is_not_destructive() -> None:
    def peek() -> str:
        return "peeked"

    toolset = FunctionToolset()
    toolset.add_tool(Tool(peek, requires_approval=True))
    options = _guarded(_Calls("peek"), tool_guard=ToolGuardConfig())
    options["toolsets"] = [toolset]

    _, state = await _parked(_server(options))

    assert [(w.kind, w.destructive) for w in state.waiting] == [("approval", False)]


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
    assert state.waiting == (
        RunWaiting(
            kind="external", summary="pick_colour", tool_call_id="call-1", destructive=False
        ),
    )


@pytest.mark.django_db(transaction=True)
async def test_a_client_tool_stamped_destructive_parks_as_destructive() -> None:
    stamped = {**_PICK, "parameters": {**_PICK["parameters"], "x-destructive": True}}
    body = _browser_body("r1", detach=True, tools=[stamped])

    state = await _browser_run(_browser_view(_Calls("pick_colour")), body)

    assert [(w.kind, w.destructive) for w in state.waiting] == [("external", True)]


@pytest.mark.django_db(transaction=True)
async def test_a_connected_browser_run_ending_on_a_client_tool_does_not_park() -> None:
    body = _browser_body("r1", tools=[_PICK])

    state = await _browser_run(_browser_view(_Calls("pick_colour")), body)

    assert (state.status, state.waiting) == (ThreadRunStatus.IDLE, ())


@pytest.mark.django_db(transaction=True)
async def test_a_connected_browser_approval_parks_then_its_answer_completes() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    view = _browser_view(calls)
    parked = await _browser_run(view, _browser_body("r1"))
    answer = [{"interruptId": "int-call-1", "status": "resolved", "payload": {"approved": True}}]

    state = await _browser_run(view, _browser_body("r2", messages=_ASKED_TO_DELETE, resume=answer))

    assert (parked.status, parked.waiting[0].kind) == (ThreadRunStatus.WAITING, "approval")
    assert (state.status, state.waiting, state.last_run_id) == (ThreadRunStatus.IDLE, (), "r2")
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
_AGAIN = [{"id": "u-again", "role": "user", "content": "again"}]


async def _resume(
    options: dict[str, Any],
    stop: Any,
    run_id: str,
    *,
    thread_id: str | None = None,
    resume: Any = _APPROVED,
    messages: Any = (),
) -> Any:
    view = DjangoAGUIView(
        options["registry"], **{k: v for k, v in options.items() if k != "registry"}
    )
    body = {
        "threadId": thread_id or stop.thread_id,
        "runId": run_id,
        "messages": list(messages),
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
        "resume": resume,
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
        (),
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

    assert (state.status, state.waiting[0].kind) == (ThreadRunStatus.WAITING, "approval")


@pytest.mark.django_db(transaction=True)
async def test_a_parked_approval_is_resumable_only_on_its_own_thread() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls)
    stop, _ = await _parked(_server(dict(options)))

    resumed, _ = await _resume(options, stop, "r-other", thread_id="t-other")
    forked, _ = await _resume(options, stop, "r-fork", thread_id="t-fork")

    assert (resumed.status_code, forked.status_code) == (404, 404)
    assert calls.ran == []
    streamed, state = await _resume(options, stop, "r-home")
    assert "deleted widget-1" in streamed
    assert calls.ran == ["widget-1"]
    assert state.last_run_id == "r-home"


@pytest.mark.django_db(transaction=True)
async def test_a_run_with_no_conversation_store_still_parks() -> None:
    options = _guarded(_Calls("delete_thing", _DELETE_ARGS), run_timeout_seconds=300)
    options["conversation_store"] = NullConversationStore()

    state = await _browser_run(
        DjangoAGUIView(options.pop("registry"), **options), _browser_body("r1")
    )

    assert (state.status, state.waiting[0].kind) == (ThreadRunStatus.WAITING, "approval")


@pytest.mark.django_db(transaction=True)
async def test_a_parked_run_whose_thread_state_is_gone_is_not_resumable() -> None:
    options = _guarded(_Calls("delete_thing", _DELETE_ARGS))
    stop, _ = await _parked(_server(dict(options)))
    await StoredThreadRun.objects.filter(thread_id=stop.thread_id).adelete()

    response, _ = await _resume(options, stop, "r-answer")

    assert response.status_code == 404


def _interleaved(monkeypatch: pytest.MonkeyPatch, outer: str, before: Any) -> None:
    claim = DefaultRunStore.claim
    done: list[bool] = []

    async def interleaved(self: DefaultRunStore, thread_id: str, run_id: str, **kwargs: Any) -> Any:
        if run_id == outer and not done:
            done.append(True)
            await before(self, thread_id, kwargs["request"])
        return await claim(self, thread_id, run_id, **kwargs)

    monkeypatch.setattr(DefaultRunStore, "claim", interleaved)


@pytest.mark.django_db(transaction=True)
async def test_two_interleaved_resumes_of_one_park_run_its_approved_call_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls)
    stop, _ = await _parked(_server(dict(options)))

    async def inner(store: Any, thread_id: str, request: Any) -> None:
        await _resume(options, stop, "r-inner")

    _interleaved(monkeypatch, "r-outer", inner)
    response, _ = await _resume(options, stop, "r-outer")

    assert calls.ran == ["widget-1"]
    assert response.status_code == 404
    assert json.loads(response.content) == {"error": "no resumable run", "run_id": stop.run_id}


@pytest.mark.django_db(transaction=True)
async def test_a_resume_that_loses_its_park_to_a_live_one_names_the_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls)
    stop, _ = await _parked(_server(dict(options)))

    async def inner(store: Any, thread_id: str, request: Any) -> None:
        claim = await store.claim(
            thread_id, "r-inner", request=request, lease=RUN_LEASE, resumes=stop.run_id
        )
        assert claim.claimed

    _interleaved(monkeypatch, "r-outer", inner)
    response, _ = await _resume(options, stop, "r-outer")

    assert response.status_code == 409
    assert json.loads(response.content)["run_id"] == "r-inner"
    assert calls.ran == []


class _PlainRunStore:
    """A run store with no ``release`` and no ``resumes=``, so it is not a resumable one."""

    def __init__(self) -> None:
        self._store = DefaultRunStore()

    async def claim(self, thread_id: str, run_id: str, *, request: Any, lease: Any) -> Any:
        return await self._store.claim(thread_id, run_id, request=request, lease=lease)

    def __getattr__(self, name: str) -> Any:
        if name == "release":
            raise AttributeError(name)
        return getattr(self._store, name)


@pytest.mark.django_db(transaction=True)
async def test_a_park_on_a_store_that_is_not_resumable_still_resumes() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = {**_guarded(calls), "run_store": _PlainRunStore()}
    stop, _ = await _parked(_server(dict(options)))

    streamed, state = await _resume(options, stop, "r-answer")

    assert "deleted widget-1" in streamed
    assert calls.ran == ["widget-1"]
    assert state.last_run_id == "r-answer"


@pytest.mark.django_db(transaction=True)
async def test_a_resume_of_a_run_that_did_not_park_is_not_claimed_as_a_park_resume() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls, tool_guard=ToolGuardConfig(enabled=False))
    stop, before = await _parked(_server(dict(options)))

    streamed, state = await _resume(options, stop, "r-next", resume=None, messages=_AGAIN)

    assert before.status is ThreadRunStatus.IDLE
    assert "RUN_FINISHED" in streamed
    assert state.last_run_id == "r-next"


@pytest.mark.django_db(transaction=True)
async def test_a_park_on_a_store_that_is_not_resumable_resumes_only_on_its_own_thread() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = {**_guarded(calls), "run_store": _PlainRunStore()}
    stop, _ = await _parked(_server(dict(options)))

    other, _ = await _resume(options, stop, "r-other", thread_id="t-other")

    assert other.status_code == 404
    assert calls.ran == []
    streamed, state = await _resume(options, stop, "r-home")
    assert "deleted widget-1" in streamed
    assert calls.ran == ["widget-1"]
    assert state.last_run_id == "r-home"


class _DeniedQueue(WorkerQueue):
    def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
        raise NotPermitted()


async def _asked(options: dict[str, Any]) -> tuple[AGUIServer, Any, Any]:
    server = _server(dict(options))
    stop, _ = await _parked(server)
    return server, stop, server.headless_request(authenticated_user())


def _answers(server: AGUIServer, stop: Any, **kwargs: Any) -> Any:
    runner = AGUIHeadlessRunner(server, **kwargs)
    return runner, {"timeout": 5, "thread_id": stop.thread_id, "answers": {"call-1": "blue"}}


async def _first_claim_runs(monkeypatch: pytest.MonkeyPatch, before: Any) -> None:
    claim = DefaultRunStore.claim
    done: list[bool] = []

    async def interleaved(self: DefaultRunStore, thread_id: str, run_id: str, **kwargs: Any) -> Any:
        if not done:
            done.append(True)
            await before(self, thread_id, kwargs["request"])
        return await claim(self, thread_id, run_id, **kwargs)

    monkeypatch.setattr(DefaultRunStore, "claim", interleaved)


@pytest.mark.django_db(transaction=True)
async def test_a_refused_queued_answer_leaves_the_question_parked_for_a_later_one() -> None:
    options = _guarded(_Calls("ask", '{"topic": "colour"}'))
    server, stop, request = await _asked(options)
    runner, kwargs = _answers(server, stop, queue=_DeniedQueue(AGUIHeadlessRunner(server)))

    with pytest.raises(NotPermitted):
        await runner.start("helper", "", authenticated_user(), **kwargs)

    state = await DefaultRunStore().get(stop.thread_id, request=request)
    assert (state.status, state.last_run_id) == (ThreadRunStatus.WAITING, stop.run_id)
    later = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "", authenticated_user(), **kwargs
    )
    assert later.outcome is RunOutcome.COMPLETED
    stored = await DefaultConversationStore().load(stop.thread_id, request=request)
    assert [m.get("content") for m in stored.messages][-2:] == ["blue", "done"]


@pytest.mark.django_db(transaction=True)
async def test_a_headless_answer_that_fails_after_its_claim_leaves_the_question_parked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, stop, request = await _asked(_guarded(_Calls("ask", '{"topic": "colour"}')))
    runner, kwargs = _answers(server, stop)

    async def failing(*args: Any, **kw: Any) -> Any:
        raise RuntimeError("the history is unreadable")

    monkeypatch.setattr(AGUIServer, "_link", failing)
    with pytest.raises(RuntimeError, match="unreadable"):
        await runner.start("helper", "", authenticated_user(), **kwargs)

    state = await DefaultRunStore().get(stop.thread_id, request=request)
    assert (state.status, state.last_run_id) == (ThreadRunStatus.WAITING, stop.run_id)


@pytest.mark.django_db(transaction=True)
async def test_two_interleaved_headless_answers_of_one_question_run_the_model_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, stop, request = await _asked(_guarded(_Calls("ask", '{"topic": "colour"}')))
    runner, kwargs = _answers(server, stop)

    async def inner(store: Any, thread_id: str, request: Any) -> None:
        await runner.run_to_first_stop("helper", "", authenticated_user(), **kwargs)

    await _first_claim_runs(monkeypatch, inner)
    with pytest.raises(RunNotParkedError) as refused:
        await runner.start("helper", "", authenticated_user(), **kwargs)

    assert (refused.value.run_id, refused.value.active_run_id) == (stop.run_id, None)
    stored = await DefaultConversationStore().load(stop.thread_id, request=request)
    assert [m.get("content") for m in stored.messages].count("done") == 1


@pytest.mark.django_db(transaction=True)
async def test_two_interleaved_queued_answers_of_one_question_run_the_model_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, stop, request = await _asked(_guarded(_Calls("ask", '{"topic": "colour"}')))
    worker = WorkerQueue(AGUIHeadlessRunner(server))
    runner, kwargs = _answers(server, stop, queue=worker)

    async def inner(store: Any, thread_id: str, request: Any) -> None:
        await runner.start("helper", "", authenticated_user(), **kwargs)
        await worker.work()

    await _first_claim_runs(monkeypatch, inner)
    with pytest.raises(RunNotParkedError):
        await runner.start("helper", "", authenticated_user(), **kwargs)
    await worker.work()

    assert len(worker.enqueued) == 1
    stored = await DefaultConversationStore().load(stop.thread_id, request=request)
    assert [m.get("content") for m in stored.messages].count("done") == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("queued", [False, True])
async def test_a_headless_answer_that_loses_its_park_to_a_live_run_names_the_holder(
    monkeypatch: pytest.MonkeyPatch, queued: bool
) -> None:
    server, stop, _ = await _asked(_guarded(_Calls("ask", '{"topic": "colour"}')))
    worker = WorkerQueue(AGUIHeadlessRunner(server))
    runner, kwargs = _answers(server, stop, **({"queue": worker} if queued else {}))

    async def inner(store: Any, thread_id: str, request: Any) -> None:
        claim = await store.claim(
            thread_id, "r-inner", request=request, lease=RUN_LEASE, resumes=stop.run_id
        )
        assert claim.claimed

    await _first_claim_runs(monkeypatch, inner)
    with pytest.raises(ThreadBusyError) as busy:
        await runner.start("helper", "", authenticated_user(), **kwargs)

    assert busy.value.run_id == "r-inner"
    assert worker.enqueued == []


@pytest.mark.django_db(transaction=True)
async def test_an_answer_to_a_thread_that_is_not_waiting_takes_a_plain_claim() -> None:
    server, stop, request = await _asked(_guarded(_Calls("ask", '{"topic": "colour"}')))
    runner, kwargs = _answers(server, stop)
    await runner.run_to_first_stop("helper", "", authenticated_user(), **kwargs)

    again = await runner.run_to_first_stop("helper", "", authenticated_user(), **kwargs)

    state = await DefaultRunStore().get(stop.thread_id, request=request)
    assert state.last_run_id == again.run_id


class _AsksTwice(_Calls):
    """Asks two questions in one response, then answers ``done`` once a tool has returned."""

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            parts = [p for m in messages for p in getattr(m, "parts", [])]
            self.ran.append("model")
            if any(getattr(p, "part_kind", "") == "tool-return" for p in parts):
                yield "done"
                return
            yield {
                i: DeltaToolCall(name="ask", json_args='{"topic": "x"}', tool_call_id=f"call-{i}")
                for i in range(2)
            }

        return FunctionModel(stream_function=stream_fn)


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.django_db(transaction=True)
async def test_a_headless_answer_leaving_a_parked_call_open_is_refused_before_its_claim(
    queued: bool,
) -> None:
    calls = _AsksTwice("ask")
    server, stop, request = await _asked(_guarded(calls))
    worker = WorkerQueue(AGUIHeadlessRunner(server))
    runner = AGUIHeadlessRunner(server, **({"queue": worker} if queued else {}))
    kwargs = {"timeout": 5, "thread_id": stop.thread_id, "answers": {"call-0": "blue"}}

    with pytest.raises(PartialAnswerError) as refused:
        await runner.start("helper", "", authenticated_user(), **kwargs)

    assert (refused.value.thread_id, refused.value.unanswered) == (stop.thread_id, ("call-1",))
    state = await DefaultRunStore().get(stop.thread_id, request=request)
    assert (state.status, state.last_run_id) == (ThreadRunStatus.WAITING, stop.run_id)
    assert [w.tool_call_id for w in state.waiting] == ["call-0", "call-1"]
    assert (calls.ran, worker.enqueued) == (["model"], [])


@pytest.mark.django_db(transaction=True)
async def test_a_headless_answer_to_every_parked_call_resumes_the_run() -> None:
    server, stop, request = await _asked(_guarded(_AsksTwice("ask")))
    kwargs = {"timeout": 5, "thread_id": stop.thread_id, "answers": {"call-0": "a", "call-1": "b"}}

    done = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "", authenticated_user(), **kwargs
    )

    assert done.outcome is RunOutcome.COMPLETED


_ONE = [{"interruptId": "int-call-0", "status": "resolved", "payload": {"approved": True}}]


@pytest.mark.parametrize(
    ("resume_route", "resume", "unanswered"),
    [(True, _ONE, ["call-1"]), (True, [], ["call-0", "call-1"]), (False, _ONE, ["call-1"])],
)
@pytest.mark.django_db(transaction=True)
async def test_a_browser_answer_leaving_a_parked_call_open_is_a_400_and_the_park_stands(
    resume_route: bool, resume: list[dict[str, Any]], unanswered: list[str]
) -> None:
    calls = _DeletesTwice("delete_thing")
    options = _guarded(calls)
    stop, _ = await _parked(_server(dict(options)))

    if resume_route:
        response, _ = await _resume(options, stop, "r-answer", resume=resume)
    else:
        view = DjangoAGUIView(options.pop("registry"), **options)
        body = {**json.loads(_browser_body("r-answer", resume=resume)), "threadId": stop.thread_id}
        response = await view(
            AuthedAsyncRequestFactory().post(
                "/agent/", data=json.dumps(body), content_type="application/json"
            )
        )

    assert response.status_code == 400
    assert json.loads(response.content)["unanswered"] == unanswered
    state = await DefaultRunStore().get(
        stop.thread_id, request=_server(_guarded(calls)).headless_request(authenticated_user())
    )
    assert (state.status, state.last_run_id) == (ThreadRunStatus.WAITING, stop.run_id)
    assert (len(state.waiting), calls.ran) == (2, [])


@pytest.mark.django_db(transaction=True)
async def test_a_client_tool_answered_by_its_tool_message_resumes_its_park() -> None:
    options = _guarded(_Calls("pick_colour"), run_timeout_seconds=300)
    view = DjangoAGUIView(
        options["registry"], **{k: v for k, v in options.items() if k != "registry"}
    )
    await _browser_run(view, _browser_body("r1", detach=True, tools=[_PICK]))
    picked = [
        {"id": "u1", "role": "user", "content": "go"},
        {
            "id": "a1",
            "role": "assistant",
            "toolCalls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "pick_colour", "arguments": "{}"},
                }
            ],
        },
        {"id": "t1", "role": "tool", "toolCallId": "call-1", "content": "blue"},
    ]
    parked = SimpleNamespace(thread_id="t-browser", run_id="r1")

    streamed, state = await _resume(options, parked, "r2", resume=[], messages=picked)

    assert "done" in streamed
    assert (state.status, state.last_run_id) == (ThreadRunStatus.IDLE, "r2")


@pytest.mark.django_db(transaction=True)
async def test_a_turn_carrying_an_old_tool_message_on_a_parked_thread_is_not_an_answer() -> None:
    calls = _DeletesTwice("delete_thing")
    options = _guarded(calls)
    stop, _ = await _parked(_server(dict(options)))
    view = DjangoAGUIView(options.pop("registry"), **options)
    old = [
        {"id": "u0", "role": "user", "content": "go"},
        {
            "id": "a0",
            "role": "assistant",
            "toolCalls": [
                {
                    "id": "call-old",
                    "type": "function",
                    "function": {"name": "ask", "arguments": "{}"},
                }
            ],
        },
        {"id": "t0", "role": "tool", "toolCallId": "call-old", "content": "x"},
        {"id": "u2", "role": "user", "content": "go on"},
    ]
    body = {**json.loads(_browser_body("r-next", messages=old)), "threadId": stop.thread_id}

    request = AuthedAsyncRequestFactory().post(
        "/agent/", data=json.dumps(body), content_type="application/json"
    )

    response = await view(request)

    assert isinstance(response, StreamingHttpResponse)
    async for _ in response:  # ty: ignore[not-iterable]
        pass
    state = await _settled(stop.thread_id, request)
    assert state.last_run_id == "r-next"


@pytest.mark.django_db(transaction=True)
async def test_a_headless_prompt_on_a_parked_thread_without_answers_takes_a_plain_claim() -> None:
    server, stop, request = await _asked(_guarded(_AsksTwice("ask")))

    again = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "something else", authenticated_user(), timeout=5, thread_id=stop.thread_id
    )

    state = await DefaultRunStore().get(stop.thread_id, request=request)
    assert state.last_run_id == again.run_id != stop.run_id
