from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django_spec_agents import IdentifierTooLongError
from django_spec_agents.contrib.store.default_child_session_index import DefaultChildSessionIndex
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.headless.types.headless_start import HeadlessStart
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from pydantic_ai.capabilities import Hooks
from pydantic_ai.models.function import AgentInfo, FunctionModel

from django_ag_ui import ToolRegistry, tool
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.claim_lapsed_error import ClaimLapsedError
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.agent.queued_start import start_from_sync
from django_ag_ui.agent.thread_busy_error import ThreadBusyError
from django_ag_ui.agent.utils import is_queued_input
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.constants import RUN_LEASE
from tests.agent.worker_queue import WorkerQueue
from tests.authed_request_factory import AuthedAsyncRequestFactory, authenticated_user

_PARENT = ThreadRef("t-parent", "planner")
_LINEAGE = ChildLineageRecord(root=_PARENT, depth=1, tools=["lookup"], gated=[])


def _server(
    offered: list[list[str]] | None = None, conversations: Any = None, **overrides: Any
) -> AGUIServer:
    registry = ToolRegistry()

    @tool(registry)
    def lookup(key: str) -> str:
        """Look a key up."""
        return key

    @tool(registry)
    def wider(key: str) -> str:
        """A tool the parent never had."""
        return key

    async def stream_fn(messages: list, info: AgentInfo) -> Any:
        if offered is not None:
            offered.append(sorted(t.name for t in info.function_tools))
        yield "answer"

    return AGUIServer(
        registry,
        model=FunctionModel(stream_function=stream_fn),
        csrf_exempt=True,
        conversation_store=conversations or DefaultConversationStore(),
        step_store=DefaultStepStore,
        run_store=DefaultRunStore(),
        config=build_ag_ui_config(heartbeat_seconds=0),
        id="helper",
        **{"inbound_queue": DefaultInboundQueue(), **overrides},
    )


def _runner_and_queue(
    server: AGUIServer | None = None, **options: Any
) -> tuple[AGUIServer, AGUIHeadlessRunner, WorkerQueue]:
    server = server or _server()
    queue = WorkerQueue(AGUIHeadlessRunner(server))
    return server, AGUIHeadlessRunner(server, queue=queue, **options), queue


async def _state(server: AGUIServer, thread_id: str) -> Any:
    request = server.headless_request(authenticated_user())
    return await DefaultRunStore().get(thread_id, request=request)


async def _log(server: AGUIServer, thread_id: str) -> tuple[Any, list[str]]:
    request = server.headless_request(authenticated_user())
    state = await DefaultRunStore().get(thread_id, request=request)
    assert state is not None
    entries = await DefaultRunStore().read(state.last_run_id, request=request)
    kept = [e for e in entries if not is_queued_input(e.event)]
    return state, [type(e.event).__name__ if hasattr(e, "event") else str(e) for e in kept]


class _RefusingQueue(WorkerQueue):
    def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
        raise RuntimeError("the queue is down")


@pytest.mark.django_db(transaction=True)
async def test_a_queued_start_holds_its_thread_before_it_returns() -> None:
    server, runner, queue = _runner_and_queue()
    user = authenticated_user()
    tasks_before = len(asyncio.all_tasks())

    started = await runner.start(
        "helper", "hi", user, timeout=7, thread_id="t1", parent=_PARENT, lineage=_LINEAGE
    )

    assert len(asyncio.all_tasks()) == tasks_before
    state = await _state(server, "t1")
    assert (state.status, state.active_run_id) == (ThreadRunStatus.RUNNING, started.run_id)
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
            "gated": [],
        },
        "run_id": started.run_id,
        "answers": None,
        "inbox_mark": 0,
    }
    assert started == HeadlessStart(thread_id="t1", run_id=started.run_id)


@pytest.mark.django_db(transaction=True)
async def test_a_queued_start_refuses_a_busy_thread_at_the_call() -> None:
    server, runner, queue = _runner_and_queue()
    user = authenticated_user()
    await DefaultRunStore().claim(
        "t1", "run-a", request=server.headless_request(user), lease=RUN_LEASE
    )

    with pytest.raises(ThreadBusyError) as busy:
        await runner.start("helper", "hi", user, timeout=5, thread_id="t1")

    assert busy.value.run_id == "run-a"
    assert queue.enqueued == []


@pytest.mark.django_db(transaction=True)
async def test_a_queued_child_is_listed_under_its_parent_once_start_returns() -> None:
    server, runner, _ = _runner_and_queue()
    user = authenticated_user()

    started = await runner.start("helper", "hi", user, timeout=5, parent=_PARENT, lineage=_LINEAGE)

    request = server.headless_request(user)
    listed = await DefaultChildSessionIndex(DefaultRunStore()).children(_PARENT, request=request)
    assert [child.thread for child in listed] == [ThreadRef(started.thread_id)]


@pytest.mark.django_db(transaction=True)
async def test_a_failed_enqueue_gives_the_thread_back() -> None:
    server = _server()
    runner = AGUIHeadlessRunner(server, queue=_RefusingQueue(AGUIHeadlessRunner(server)))

    with pytest.raises(RuntimeError, match="the queue is down"):
        await runner.start("helper", "hi", authenticated_user(), timeout=5, thread_id="t1")

    state = await _state(server, "t1")
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)


class _FailingSave(DefaultConversationStore):
    async def save(self, conversation: Any, *, request: Any) -> None:
        raise RuntimeError("the store is down")


@pytest.mark.django_db(transaction=True)
async def test_a_parent_that_fails_to_save_gives_the_thread_back() -> None:
    server = _server(conversations=_FailingSave())
    _, runner, queue = _runner_and_queue(server)

    with pytest.raises(RuntimeError, match="the store is down"):
        await runner.start(
            "helper", "hi", authenticated_user(), timeout=5, thread_id="t1", parent=_PARENT
        )

    state = await _state(server, "t1")
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)
    assert queue.enqueued == []


@pytest.mark.django_db(transaction=True)
async def test_an_over_long_thread_id_is_refused_at_the_call() -> None:
    server, runner, queue = _runner_and_queue()

    with pytest.raises(IdentifierTooLongError):
        await runner.start("helper", "hi", authenticated_user(), timeout=5, thread_id="t" * 300)

    assert queue.enqueued == []


async def test_a_queued_start_refuses_capabilities_it_cannot_carry() -> None:
    _, runner, queue = _runner_and_queue()

    with pytest.raises(ValueError, match="no capabilities"):
        await runner.start("helper", "hi", authenticated_user(), timeout=5, capabilities=[Hooks()])

    assert queue.enqueued == []


@pytest.mark.django_db(transaction=True)
async def test_the_worker_takes_up_the_claimed_run_and_writes_what_in_process_does() -> None:
    server, runner, queue = _runner_and_queue()
    user = authenticated_user()

    queued = await runner.start("helper", "hi", user, timeout=5, parent=_PARENT, lineage=_LINEAGE)
    [result] = await queue.work()
    direct = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "hi", user, timeout=5, parent=_PARENT
    )

    assert result.service_result.outcome is RunOutcome.COMPLETED
    assert result.service_result.run_id == queued.run_id
    queued_state, queued_log = await _log(server, queued.thread_id)
    direct_state, direct_log = await _log(server, direct.thread_id)
    assert queued_state.status is direct_state.status is ThreadRunStatus.IDLE
    assert queued_state.last_run_id == queued.run_id
    assert queued_log == direct_log
    assert queued_log


@pytest.mark.django_db(transaction=True)
async def test_a_queued_child_keeps_its_parents_bounds_on_the_worker() -> None:
    offered: list[list[str]] = []
    _, runner, queue = _runner_and_queue(_server(offered))

    await runner.start(
        "helper", "hi", authenticated_user(), timeout=5, parent=_PARENT, lineage=_LINEAGE
    )
    await queue.work()

    assert offered == [["lookup"]]


@pytest.mark.django_db(transaction=True)
async def test_a_runner_takes_up_a_queued_claim_by_its_run_id() -> None:
    offered: list[list[str]] = []
    server, runner, _ = _runner_and_queue(_server(offered))
    user = authenticated_user()
    started = await runner.start("helper", "hi", user, timeout=5, parent=_PARENT, lineage=_LINEAGE)

    stop = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper",
        "hi",
        user,
        timeout=5,
        thread_id=started.thread_id,
        parent=_PARENT,
        lineage=_LINEAGE,
        run_id=started.run_id,
    )

    assert (stop.run_id, stop.outcome) == (started.run_id, RunOutcome.COMPLETED)
    assert offered == [["lookup"]]
    state = await _state(server, started.thread_id)
    assert (state.status, state.last_run_id) == (ThreadRunStatus.IDLE, started.run_id)


@pytest.mark.django_db(transaction=True)
async def test_a_run_whose_queued_claim_lapsed_is_not_run() -> None:
    offered: list[list[str]] = []
    server = _server(offered)
    _, runner, _ = _runner_and_queue(server, queued_lease=timedelta(0))
    user = authenticated_user()
    started = await runner.start("helper", "hi", user, timeout=5, thread_id="t1")

    with pytest.raises(ClaimLapsedError) as lapsed:
        await AGUIHeadlessRunner(server).run_to_first_stop(
            "helper", "hi", user, timeout=5, thread_id="t1", run_id=started.run_id
        )

    assert (lapsed.value.thread_id, lapsed.value.run_id) == ("t1", started.run_id)
    assert offered == []


@pytest.mark.django_db(transaction=True)
async def test_the_spec_name_is_the_hosts_to_choose() -> None:
    _, runner, queue = _runner_and_queue(spec_name="agent_start")

    await runner.start("helper", "hi", authenticated_user(), timeout=5)

    assert queue.enqueued[0][0] == "agent_start"


@pytest.mark.django_db(transaction=True)
def test_a_sync_start_on_a_queued_runner_claims_and_enqueues() -> None:
    server, runner, queue = _runner_and_queue()

    started = start_from_sync(runner, "helper", "hi", authenticated_user(), timeout=5)

    state = async_to_sync(_state)(server, started.thread_id)
    assert (state.status, state.active_run_id) == (ThreadRunStatus.RUNNING, started.run_id)
    assert queue.enqueued[0][1]["run_id"] == started.run_id


@pytest.mark.django_db(transaction=True)
def test_a_sync_start_with_no_queue_runs_to_its_first_stop() -> None:
    server = _server()

    started = start_from_sync(
        AGUIHeadlessRunner(server), "helper", "hi", authenticated_user(), timeout=5, thread_id="t2"
    )

    state = async_to_sync(_state)(server, "t2")
    assert state.status is ThreadRunStatus.IDLE
    assert state.last_run_id == started.run_id


@pytest.mark.django_db(transaction=True)
async def test_a_cancel_recorded_while_queued_ends_the_run_before_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offered: list[list[str]] = []
    server, runner, _ = _runner_and_queue(_server(offered))
    user = authenticated_user()
    started = await runner.start("helper", "hi", user, timeout=5, thread_id="t-c")
    assert await runner.cancel("t-c", user)
    reached: list[str] = []
    real_start = server.start_headless

    async def start_headless(*args: Any, **kwargs: Any) -> Any:
        reached.append("start_headless")
        return await real_start(*args, **kwargs)

    monkeypatch.setattr(server, "start_headless", start_headless)

    stop = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "hi", user, timeout=5, thread_id="t-c", run_id=started.run_id
    )

    assert stop.outcome is RunOutcome.CANCELLED
    assert (offered, reached) == ([], [])
    state = await _state(server, "t-c")
    assert (state.status, state.active_run_id, state.cancel_requested) == (
        ThreadRunStatus.IDLE,
        None,
        False,
    )


@pytest.mark.django_db(transaction=True)
async def test_a_queued_child_on_a_mount_with_no_inbound_queue_is_refused_before_it_claims() -> (
    None
):
    server = _server(inbound_queue=None)
    _, runner, queue = _runner_and_queue(server)

    with pytest.raises(ImproperlyConfigured, match="no inbound_queue="):
        await runner.start(
            "helper", "hi", authenticated_user(), timeout=5, thread_id="t1", lineage=_LINEAGE
        )

    assert await _state(server, "t1") is None
    assert queue.enqueued == []


@pytest.mark.django_db(transaction=True)
def test_a_sync_start_enqueues_inside_the_callers_transaction() -> None:
    server, runner, queue = _runner_and_queue()

    with pytest.raises(RuntimeError, match="the caller rolls back"), transaction.atomic():
        start_from_sync(runner, "helper", "hi", authenticated_user(), timeout=5, thread_id="t-tx")
        assert len(queue.enqueued) == 1
        raise RuntimeError("the caller rolls back")

    assert async_to_sync(_state)(server, "t-tx") is None


@pytest.mark.django_db(transaction=True)
async def test_a_queued_start_on_a_mount_with_no_inbox_keeps_no_mark() -> None:
    server, runner, queue = _runner_and_queue(_server(inbound_queue=None))
    user = authenticated_user()

    started = await runner.start("helper", "hi", user, timeout=5)
    request = server.headless_request(user)
    log = await DefaultRunStore().read(started.run_id, request=request)
    [result] = await queue.work()

    [(_, params, _)] = queue.enqueued
    assert (params["inbox_mark"], log) == (None, [])
    assert result.service_result.outcome is RunOutcome.COMPLETED


@pytest.mark.django_db(transaction=True)
async def test_a_queued_starts_kept_mark_is_never_replayed() -> None:
    server, runner, queue = _runner_and_queue()
    started = await runner.start("helper", "hi", authenticated_user(), timeout=5)
    await queue.work()
    patterns, _, _ = server.urls
    events = next(p.callback for p in patterns if p.name == "thread_events")
    request = AuthedAsyncRequestFactory().get(f"/agent/threads/{started.thread_id}/events/")

    page = json.loads((await events(request, thread_id=started.thread_id)).content)
    log = await DefaultRunStore().read(started.run_id, request=request)

    assert is_queued_input(log[0].event)
    assert log[0].event["value"] == {"inbox_mark": 0}
    assert [e["seq"] for e in page["events"]] == [entry.seq for entry in log[1:]]
