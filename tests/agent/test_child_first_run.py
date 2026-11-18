from __future__ import annotations

import asyncio
from typing import Any

import pytest
from django_spec_agents.child_sessions.types.inbound_kind import InboundKind
from django_spec_agents.child_sessions.waking_inbound_queue import WakingInboundQueue
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from django_ag_ui import ToolRegistry, tool
from django_ag_ui.agent import agui_server
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.agent.queued_start import start_from_sync
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.worker_queue import WorkerQueue
from tests.authed_request_factory import authenticated_user

_PARENT = ThreadRef("t-parent", "planner")
_LINEAGE = ChildLineageRecord(root=_PARENT, depth=1, tools=["lookup"], gated=[])
_ASKED = [
    {"id": "u0", "role": "user", "content": "plan it"},
    {
        "id": "a0",
        "role": "assistant",
        "toolCalls": [
            {"id": "c1", "type": "function", "function": {"name": "ask", "arguments": "{}"}}
        ],
    },
]


class _Model:
    """Records what each request was sent and offered; ``during`` runs inside the first one."""

    def __init__(self, during: Any = None, *, fail: bool = False) -> None:
        self.sent: list[list[Any]] = []
        self.offered: list[list[str]] = []
        self._during = during
        self._fail = fail

    def build(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            self.sent.append(list(messages))
            self.offered.append(sorted(t.name for t in info.function_tools))
            if self._during is not None and len(self.sent) == 1:
                await self._during()
            if self._fail:
                raise RuntimeError("the model is down")
            yield f"answer {len(self.sent)}"

        return FunctionModel(stream_function=stream_fn)


def _server(model: _Model, queue: Any) -> AGUIServer:
    registry = ToolRegistry()

    @tool(registry)
    def lookup(key: str) -> str:
        """Look a key up."""
        return key

    @tool(registry)
    def wider(key: str) -> str:
        """A tool the parent never had."""
        return key

    return AGUIServer(
        registry,
        model=model.build(),
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        step_store=DefaultStepStore,
        run_store=DefaultRunStore(),
        config=build_ag_ui_config(heartbeat_seconds=0),
        id="helper",
        inbound_queue=queue,
    )


async def _settled(server: AGUIServer, thread_id: str, runs: int = 1) -> Any:
    request = server.headless_request(authenticated_user())
    for _ in range(300):
        state = await DefaultRunStore().get(thread_id, request=request)
        if state is not None and state.status is not ThreadRunStatus.RUNNING:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("the run never settled")


async def _reports(queue: DefaultInboundQueue, user: Any) -> list[Any]:
    request = _server(_Model(), queue).headless_request(user)
    return await queue.pending(_PARENT, request=request)


@pytest.mark.django_db(transaction=True)
async def test_a_childs_first_in_process_run_keeps_its_parents_bounds_and_reports() -> None:
    model, queue = _Model(), DefaultInboundQueue()
    server = _server(model, queue)
    user = authenticated_user()

    started = await AGUIHeadlessRunner(server).start(
        "helper", "go", user, timeout=5, thread_id="t-child", parent=_PARENT, lineage=_LINEAGE
    )
    await _settled(server, started.thread_id)

    assert model.offered == [["lookup"]]
    reports = await _reports(queue, user)
    assert [(m.kind, m.sender) for m in reports] == [
        (InboundKind.REPORT, ThreadRef("t-child", "helper"))
    ]
    assert "joined" not in reports[0].body["text"]


@pytest.mark.django_db(transaction=True)
async def test_a_childs_first_queued_run_keeps_its_parents_bounds_and_reports() -> None:
    model, queue = _Model(), DefaultInboundQueue()
    server = _server(model, queue)
    worker = WorkerQueue(AGUIHeadlessRunner(server))
    user = authenticated_user()

    await AGUIHeadlessRunner(server, queue=worker).start(
        "helper", "go", user, timeout=5, thread_id="t-child", parent=_PARENT, lineage=_LINEAGE
    )
    await worker.work()

    assert model.offered == [["lookup"]]
    assert [m.kind for m in await _reports(queue, user)] == [InboundKind.REPORT]


async def _seed_asked(server: AGUIServer, user: Any) -> None:
    asked = Conversation("t-asked", messages=_ASKED, owner_id=str(user.pk))
    await DefaultConversationStore().save(asked, request=server.headless_request(user))


def _resumed_with(model: _Model, answer: str) -> None:
    [sent] = model.sent
    last = sent[-1]
    assert isinstance(last, ModelRequest)
    returns = [p for p in last.parts if isinstance(p, ToolReturnPart)]
    assert [(p.tool_call_id, p.content) for p in returns] == [("c1", answer)]
    prompts = [p.content for m in sent if isinstance(m, ModelRequest) for p in m.parts]
    assert "unsent" not in prompts and not isinstance(last.parts[-1], UserPromptPart)


@pytest.mark.django_db(transaction=True)
async def test_an_answer_resumes_the_parked_call_with_no_new_prompt() -> None:
    model = _Model()
    server = _server(model, DefaultInboundQueue())
    user = authenticated_user()
    await _seed_asked(server, user)

    stop = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "unsent", user, timeout=5, thread_id="t-asked", answers={"c1": "blue"}
    )

    assert stop.outcome is RunOutcome.COMPLETED
    _resumed_with(model, "blue")


@pytest.mark.django_db(transaction=True)
async def test_an_answer_crosses_the_queue_and_resumes_on_the_worker() -> None:
    model = _Model()
    server = _server(model, DefaultInboundQueue())
    worker = WorkerQueue(AGUIHeadlessRunner(server))
    user = authenticated_user()
    await _seed_asked(server, user)

    await AGUIHeadlessRunner(server, queue=worker).start(
        "helper", "unsent", user, timeout=5, thread_id="t-asked", answers={"c1": "blue"}
    )
    [(_, params, _)] = worker.enqueued
    await worker.work()

    assert params["answers"] == {"c1": "blue"}
    _resumed_with(model, "blue")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("waking", [False, True])
async def test_mail_that_lands_after_the_last_read_wakes_the_thread_once(waking: bool) -> None:
    inner = DefaultInboundQueue()
    user = authenticated_user()
    holder: dict[str, AGUIServer] = {}

    async def mail() -> None:
        request = holder["server"].headless_request(user)
        await inner.enqueue(
            ThreadRef("t-w", "helper"), InboundKind.STEER, {"text": "hi"}, request=request
        )

    model = _Model(mail)
    queue: Any = inner
    if waking:
        queue = WakingInboundQueue(inner, runner=None, runs=DefaultRunStore(), timeout=5)
    server = holder["server"] = _server(model, queue)

    await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "go", user, timeout=5, thread_id="t-w"
    )
    for _ in range(300):
        if len(model.sent) == 2:
            break
        await asyncio.sleep(0.01)
    await _settled(server, "t-w")
    await asyncio.sleep(0.05)

    assert len(model.sent) == 2
    assert "arrived while it was idle" in str(model.sent[1][-1])


@pytest.mark.django_db(transaction=True)
async def test_mail_pending_before_the_run_does_not_wake_it_again() -> None:
    queue = DefaultInboundQueue()
    model = _Model()
    server = _server(model, queue)
    user = authenticated_user()
    request = server.headless_request(user)
    await queue.enqueue(ThreadRef("t-w", "helper"), InboundKind.STEER, {"x": 1}, request=request)

    await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "go", user, timeout=5, thread_id="t-w"
    )
    await asyncio.sleep(0.1)

    assert len(model.sent) == 1


@pytest.mark.django_db(transaction=True)
async def test_a_failed_run_does_not_wake_its_thread() -> None:
    queue = DefaultInboundQueue()
    user = authenticated_user()
    holder: dict[str, AGUIServer] = {}

    async def mail() -> None:
        request = holder["server"].headless_request(user)
        await queue.enqueue(
            ThreadRef("t-f", "helper"), InboundKind.STEER, {"x": 1}, request=request
        )

    model = _Model(mail, fail=True)
    server = holder["server"] = _server(model, queue)

    stop = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "go", user, timeout=5, thread_id="t-f"
    )
    await asyncio.sleep(0.1)

    assert stop.outcome is RunOutcome.ERROR
    assert len(model.sent) == 1


@pytest.mark.django_db(transaction=True)
def test_a_wake_from_a_sync_bridge_runs_before_the_bridge_returns() -> None:
    inner = DefaultInboundQueue()
    user = authenticated_user()
    holder: dict[str, AGUIServer] = {}

    async def mail() -> None:
        request = holder["server"].headless_request(user)
        await inner.enqueue(
            ThreadRef("t-s", "helper"), InboundKind.STEER, {"x": 1}, request=request
        )

    model = _Model(mail)
    server = holder["server"] = _server(model, inner)

    start_from_sync(AGUIHeadlessRunner(server), "helper", "go", user, timeout=5, thread_id="t-s")

    assert len(model.sent) == 2
    assert "arrived while it was idle" in str(model.sent[1][-1])


@pytest.mark.django_db(transaction=True)
async def test_a_wake_hands_wake_thread_the_queue_inside_a_waking_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = DefaultInboundQueue()
    waking = WakingInboundQueue(inner, runner=None, runs=DefaultRunStore(), timeout=5)
    server = _server(_Model(), waking)
    request = server.headless_request(authenticated_user())
    await inner.enqueue(ThreadRef("t-q", "helper"), InboundKind.STEER, {"x": 1}, request=request)
    handed: list[Any] = []

    async def wake_thread(thread: ThreadRef, **kwargs: Any) -> None:
        handed.append(kwargs["queue"])

    monkeypatch.setattr(agui_server, "wake_thread", wake_thread)
    await server.wake(request, "t-q", after=0, runner=AGUIHeadlessRunner(server), timeout=5)

    assert handed == [inner]
