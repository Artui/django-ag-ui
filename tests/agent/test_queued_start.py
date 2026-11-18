from __future__ import annotations

import asyncio
from typing import Any

import pytest
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.headless.types.headless_start import HeadlessStart
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.capabilities import Hooks
from pydantic_ai.models.function import AgentInfo, FunctionModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.agent.queued_start import enqueue_headless, start_from_sync
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.worker_queue import WorkerQueue
from tests.authed_request_factory import authenticated_user

_PARENT = ThreadRef("t-parent", "planner")
_LINEAGE = ChildLineageRecord(root=_PARENT, depth=1, tools=["lookup"], gated=["lookup"])


def _server() -> AGUIServer:
    async def stream_fn(messages: list, info: AgentInfo) -> Any:
        yield "answer"

    return AGUIServer(
        ToolRegistry(),
        model=FunctionModel(stream_function=stream_fn),
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        step_store=DefaultStepStore,
        run_store=DefaultRunStore(),
        config=build_ag_ui_config(heartbeat_seconds=0),
        id="helper",
    )


def _runner_and_queue() -> tuple[AGUIServer, AGUIHeadlessRunner, WorkerQueue]:
    server = _server()
    plain = AGUIHeadlessRunner(server)
    queue = WorkerQueue(plain)
    return server, AGUIHeadlessRunner(server, queue=queue), queue


async def _log(server: AGUIServer, thread_id: str, user: Any) -> tuple[Any, list[str]]:
    request = server.headless_request(user)
    state = await DefaultRunStore().get(thread_id, request=request)
    assert state is not None
    entries = await DefaultRunStore().read(state.last_run_id, request=request)
    return state, [type(e.event).__name__ if hasattr(e, "event") else str(e) for e in entries]


@pytest.mark.django_db(transaction=True)
async def test_a_start_with_a_queue_enqueues_the_firing_and_starts_no_task() -> None:
    server, runner, queue = _runner_and_queue()
    user = authenticated_user()
    tasks_before = len(asyncio.all_tasks())

    started = await runner.start(
        "helper", "hi", user, timeout=7, thread_id="t1", parent=_PARENT, lineage=_LINEAGE
    )

    assert len(asyncio.all_tasks()) == tasks_before
    [(spec_name, params, actor)] = queue.enqueued
    assert (spec_name, actor) == ("run_headless", user)
    assert params == {
        "agent_id": "helper",
        "prompt": "hi",
        "timeout": 7,
        "thread_id": "t1",
        "parent": {"thread_id": "t-parent", "scope": "planner"},
        "lineage": {
            "root": {"thread_id": "t-parent", "scope": "planner"},
            "depth": 1,
            "tools": ["lookup"],
            "gated": ["lookup"],
        },
    }
    assert started == HeadlessStart(thread_id="t1", run_id=started.run_id)
    assert queue.state(started.run_id) is not None
    state = await DefaultRunStore().get("t1", request=server.headless_request(user))
    assert state is None


@pytest.mark.django_db(transaction=True)
async def test_a_start_without_a_thread_id_names_the_thread_the_worker_will_use() -> None:
    server, runner, queue = _runner_and_queue()
    user = authenticated_user()

    started = await runner.start("helper", "hi", user, timeout=5)
    await queue.work()

    assert queue.enqueued[0][1]["thread_id"] == started.thread_id
    state = await DefaultRunStore().get(started.thread_id, request=server.headless_request(user))
    assert state is not None


@pytest.mark.django_db(transaction=True)
async def test_the_worker_writes_the_lease_and_log_an_in_process_run_does() -> None:
    server, runner, queue = _runner_and_queue()
    user = authenticated_user()

    queued = await runner.start("helper", "hi", user, timeout=5, parent=_PARENT, lineage=_LINEAGE)
    [result] = await queue.work()
    direct = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "hi", user, timeout=5, parent=_PARENT, lineage=_LINEAGE
    )

    assert result.service_result.outcome is RunOutcome.COMPLETED
    queued_state, queued_log = await _log(server, queued.thread_id, user)
    direct_state, direct_log = await _log(server, direct.thread_id, user)
    assert queued_state.status is direct_state.status is ThreadRunStatus.IDLE
    assert queued_log == direct_log
    assert queued_log
    conversation = await DefaultConversationStore().load(
        queued.thread_id, request=server.headless_request(user)
    )
    assert (conversation.parent, conversation.lineage) == (_PARENT, _LINEAGE)


@pytest.mark.django_db(transaction=True)
async def test_a_start_without_a_queue_runs_in_process_as_before() -> None:
    server = _server()
    user = authenticated_user()

    started = await AGUIHeadlessRunner(server).start("helper", "hi", user, timeout=5)

    request = server.headless_request(user)
    for _ in range(200):
        state = await DefaultRunStore().get(started.thread_id, request=request)
        if state is not None and state.status is ThreadRunStatus.IDLE:
            break
        await asyncio.sleep(0.01)
    assert state is not None
    assert state.status is ThreadRunStatus.IDLE


async def test_a_queued_start_refuses_capabilities_it_cannot_carry() -> None:
    _, runner, queue = _runner_and_queue()

    with pytest.raises(ValueError, match="no capabilities"):
        await runner.start("helper", "hi", authenticated_user(), timeout=5, capabilities=[Hooks()])

    assert queue.enqueued == []


async def test_the_spec_name_is_the_hosts_to_choose() -> None:
    server = _server()
    queue = WorkerQueue(AGUIHeadlessRunner(server))
    runner = AGUIHeadlessRunner(server, queue=queue, spec_name="agent_start")

    await runner.start("helper", "hi", authenticated_user(), timeout=5)

    assert queue.enqueued[0][0] == "agent_start"


def test_enqueue_headless_is_callable_from_sync_code() -> None:
    queue = WorkerQueue(AGUIHeadlessRunner(_server()))

    started = enqueue_headless(queue, "helper", "hi", authenticated_user(), timeout=5)

    assert queue.state(started.run_id) is not None


def test_a_sync_start_with_a_queue_enqueues() -> None:
    server = _server()
    queue = WorkerQueue(AGUIHeadlessRunner(server))

    started = start_from_sync(
        AGUIHeadlessRunner(server), queue, "helper", "hi", authenticated_user(), timeout=5
    )

    assert queue.state(started.run_id) is not None


@pytest.mark.django_db(transaction=True)
def test_a_sync_start_with_no_queue_runs_to_its_first_stop() -> None:
    server = _server()
    user = authenticated_user()

    started = start_from_sync(
        AGUIHeadlessRunner(server), None, "helper", "hi", user, timeout=5, thread_id="t-sync"
    )

    assert started.thread_id == "t-sync"
    request = server.headless_request(user)
    from asgiref.sync import async_to_sync

    state = async_to_sync(DefaultRunStore().get)("t-sync", request=request)
    assert state is not None
    assert state.status is ThreadRunStatus.IDLE
    assert state.last_run_id == started.run_id
