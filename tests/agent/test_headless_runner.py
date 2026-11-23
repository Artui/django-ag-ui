from __future__ import annotations

import asyncio
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import HttpRequest
from django_spec_agents.child_sessions.child_sessions_toolset import ChildSessionsToolset
from django_spec_agents.child_sessions.types.child_session_limits import ChildSessionLimits
from django_spec_agents.child_sessions.types.inbound_kind import InboundKind
from django_spec_agents.contrib.store.default_child_session_index import DefaultChildSessionIndex
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

import django_ag_ui.agent.run_owner as run_owner_module
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.agent.thread_busy_error import ThreadBusyError
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.constants import RUN_LEASE
from tests.authed_request_factory import authenticated_user


class _Model:
    """Answers with how many messages it was sent; parks while ``park`` is set."""

    def __init__(self) -> None:
        self.park = False
        self.seen: list[int] = []

    def build(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            self.seen.append(len(messages))
            yield f"answer {len(self.seen)}"
            if self.park:
                await asyncio.Event().wait()

        return FunctionModel(stream_function=stream_fn)


def _server(model: _Model | None = None, **overrides: Any) -> AGUIServer:
    options: dict[str, Any] = {
        "model": (model or _Model()).build(),
        "csrf_exempt": True,
        "conversation_store": DefaultConversationStore(),
        "step_store": DefaultStepStore,
        "run_store": DefaultRunStore(),
        "config": build_ag_ui_config(heartbeat_seconds=0),
        "id": "helper",
    }
    options.update(overrides)
    return AGUIServer(ToolRegistry(), **options)


async def _settled(server: AGUIServer, thread_id: str) -> None:
    request = server.headless_request(authenticated_user())
    for _ in range(200):
        state = await DefaultRunStore().get(thread_id, request=request)
        if state is not None and state.status is not ThreadRunStatus.RUNNING:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never settled")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"id": None}, "no id="),
        ({"run_store": None}, "no run_store="),
        ({"step_store": None}, "no step_store="),
        ({"conversation_store": None}, "keeps nothing"),
        ({"conversation_store": DjangoSessionConversationStore()}, "browser session"),
    ],
)
def test_a_mount_that_cannot_own_a_headless_run_is_refused_at_startup(
    overrides: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ImproperlyConfigured, match=reason):
        AGUIHeadlessRunner(_server(**overrides))


def test_two_mounts_may_not_share_an_id() -> None:
    with pytest.raises(ImproperlyConfigured, match="share the id 'helper'"):
        AGUIHeadlessRunner(_server(), _server())


def test_the_synthetic_request_carries_the_principal_and_no_session() -> None:
    user = authenticated_user()

    request = _server().headless_request(user)

    assert request.user is user
    assert not hasattr(request, "session")


async def test_an_anonymous_principal_is_refused() -> None:
    runner = AGUIHeadlessRunner(_server())

    with pytest.raises(PermissionDenied):
        await runner.start("helper", "hi", object(), timeout=5)


@pytest.mark.parametrize("timeout", [0, -1])
async def test_a_headless_run_needs_a_positive_timeout(timeout: float) -> None:
    runner = AGUIHeadlessRunner(_server())

    with pytest.raises(ValueError, match="positive timeout"):
        await runner.start("helper", "hi", authenticated_user(), timeout=timeout)


async def test_an_unknown_mount_is_refused() -> None:
    with pytest.raises(LookupError, match="'other'"):
        await AGUIHeadlessRunner(_server()).start("other", "hi", authenticated_user(), timeout=5)


@pytest.mark.django_db(transaction=True)
async def test_a_headless_run_continues_the_threads_stored_history() -> None:
    model = _Model()
    server = _server(model)
    runner = AGUIHeadlessRunner(server)
    user = authenticated_user()

    first = await runner.run_to_first_stop("helper", "one", user, timeout=5)
    second = await runner.run_to_first_stop(
        "helper", "two", user, timeout=5, thread_id=first.thread_id
    )

    assert (first.outcome, second.outcome) == (RunOutcome.COMPLETED, RunOutcome.COMPLETED)
    assert second.thread_id == first.thread_id
    assert model.seen == [1, 3]
    request = server.headless_request(user)
    conversation = await DefaultConversationStore().load(first.thread_id, request=request)
    assert [m["content"] for m in conversation.messages] == ["one", "answer 1", "two", "answer 2"]


@pytest.mark.django_db(transaction=True)
async def test_a_second_start_on_a_running_thread_names_the_run() -> None:
    model = _Model()
    model.park = True
    server = _server(model)
    runner = AGUIHeadlessRunner(server)
    user = authenticated_user()
    started = await runner.start("helper", "one", user, timeout=0.2)

    with pytest.raises(ThreadBusyError) as refused:
        await runner.start("helper", "two", user, timeout=5, thread_id=started.thread_id)

    assert refused.value.run_id == started.run_id
    await _settled(server, started.thread_id)


@pytest.mark.django_db(transaction=True)
async def test_a_headless_run_past_its_timeout_ends_timed_out() -> None:
    model = _Model()
    model.park = True
    server = _server(model)

    stop = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "one", authenticated_user(), timeout=0.05
    )

    assert stop.outcome is RunOutcome.TIMED_OUT
    state = await DefaultRunStore().get(
        stop.thread_id, request=server.headless_request(authenticated_user())
    )
    assert state is not None
    assert state.status is ThreadRunStatus.IDLE


@pytest.mark.django_db(transaction=True)
async def test_a_recorded_cancel_reaches_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_owner_module, "_WATCH_SECONDS", 0.01)
    model = _Model()
    model.park = True
    runner = AGUIHeadlessRunner(_server(model))
    user = authenticated_user()
    stopping = asyncio.ensure_future(
        runner.run_to_first_stop("helper", "one", user, timeout=5, thread_id="t-cancel")
    )
    while not model.seen:
        await asyncio.sleep(0.01)

    assert await runner.cancel("t-cancel", user) is True
    stop = await asyncio.wait_for(stopping, timeout=5)

    assert stop.outcome is RunOutcome.CANCELLED
    assert await runner.cancel("t-cancel", user) is False


class _OtherRunFinishesAtClaim(DefaultRunStore):
    """Lets another run of the thread save its messages and finish as this run claims."""

    def __init__(self, conversations: DefaultConversationStore, other_run: str) -> None:
        super().__init__()
        self._conversations = conversations
        self._other: str | None = other_run

    async def claim(self, thread_id: str, run_id: str, **kwargs: Any) -> Any:
        if self._other is not None and run_id != self._other:
            other, self._other = self._other, None
            request = kwargs["request"]
            said = [
                {"id": "a1", "role": "user", "content": "from a"},
                {"id": "a2", "role": "assistant", "content": "a answered"},
            ]
            finished = Conversation(thread_id=thread_id, messages=said, owner_id="1")
            await self._conversations.save(finished, request=request)
            await self.finish(thread_id, other, RunOutcome.COMPLETED, request=request)
        return await super().claim(thread_id, run_id, **kwargs)


class _FailingLoad(DefaultConversationStore):
    async def load(self, thread_id: str, *, request: HttpRequest) -> Any:
        raise RuntimeError("the store is down")


@pytest.mark.django_db(transaction=True)
async def test_a_run_finishing_as_a_headless_start_claims_keeps_both_runs_messages() -> None:
    conversations = DefaultConversationStore()
    runs = _OtherRunFinishesAtClaim(conversations, "run-a")
    server = _server(conversation_store=conversations, run_store=runs)
    user = authenticated_user()
    request = server.headless_request(user)
    await runs.claim("t-race", "run-a", request=request, lease=RUN_LEASE)

    stop = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "two", user, timeout=5, thread_id="t-race"
    )

    assert stop.outcome is RunOutcome.COMPLETED
    conversation = await conversations.load("t-race", request=request)
    assert [m["content"] for m in conversation.messages] == [
        "from a",
        "a answered",
        "two",
        "answer 1",
    ]


@pytest.mark.django_db(transaction=True)
async def test_a_history_that_fails_to_load_gives_the_thread_back() -> None:
    server = _server(conversation_store=_FailingLoad())
    user = authenticated_user()

    with pytest.raises(RuntimeError, match="the store is down"):
        await AGUIHeadlessRunner(server).start("helper", "one", user, timeout=5, thread_id="t-f")

    state = await DefaultRunStore().get("t-f", request=server.headless_request(user))
    assert state is not None
    assert state.status is not ThreadRunStatus.RUNNING
    assert state.active_run_id is None


_PARENT = ThreadRef("t-parent", "planner")
_LINEAGE = ChildLineageRecord(root=_PARENT, depth=1, tools=["lookup", "write"], gated=["write"])


@pytest.mark.django_db(transaction=True)
async def test_a_child_is_listed_under_its_cross_agent_parent_once_start_returns() -> None:
    model = _Model()
    model.park = True
    server = _server(model)
    user = authenticated_user()
    request = server.headless_request(user)
    index = DefaultChildSessionIndex(DefaultRunStore())

    started = await AGUIHeadlessRunner(server).start(
        "helper", "one", user, timeout=0.2, parent=_PARENT
    )
    listed = await index.children(_PARENT, request=request)
    await _settled(server, started.thread_id)

    assert [child.thread for child in listed] == [ThreadRef(started.thread_id)]
    conversation = await DefaultConversationStore().load(started.thread_id, request=request)
    assert conversation.parent == _PARENT


@pytest.mark.django_db(transaction=True)
async def test_a_childs_lineage_outlives_its_runs_saves() -> None:
    queue = DefaultInboundQueue()
    server = _server(inbound_queue=queue)
    runner = AGUIHeadlessRunner(server)
    user = authenticated_user()

    first = await runner.run_to_first_stop(
        "helper", "one", user, timeout=5, parent=_PARENT, lineage=_LINEAGE
    )
    await runner.run_to_first_stop("helper", "two", user, timeout=5, thread_id=first.thread_id)

    request = server.headless_request(user)
    conversation = await DefaultConversationStore().load(first.thread_id, request=request)
    assert [m["content"] for m in conversation.messages] == ["one", "answer 1", "two", "answer 2"]
    assert (conversation.parent, conversation.lineage) == (_PARENT, _LINEAGE)
    reports = await queue.pending(_PARENT, request=request)
    assert [(m.kind, m.sender) for m in reports] == [
        (InboundKind.REPORT, ThreadRef(first.thread_id, "helper"))
    ] * 2


def _unbuildable(registry: Any, config: Any) -> Any:
    raise RuntimeError("the agent cannot be built")


@pytest.mark.django_db(transaction=True)
async def test_an_agent_that_fails_to_build_gives_the_thread_back() -> None:
    server = _server(agent_factory=_unbuildable)
    runner = AGUIHeadlessRunner(server)
    user = authenticated_user()

    for _ in range(2):
        with pytest.raises(RuntimeError, match="cannot be built"):
            await runner.start("helper", "one", user, timeout=5, thread_id="t-b")

    state = await DefaultRunStore().get("t-b", request=server.headless_request(user))
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)


@pytest.mark.django_db(transaction=True)
async def test_a_child_on_a_mount_with_no_inbound_queue_is_refused_before_it_claims() -> None:
    server = _server()
    user = authenticated_user()

    with pytest.raises(ImproperlyConfigured, match="no inbound_queue="):
        await AGUIHeadlessRunner(server).start(
            "helper", "one", user, timeout=5, thread_id="t-q", parent=_PARENT, lineage=_LINEAGE
        )

    assert await DefaultRunStore().get("t-q", request=server.headless_request(user)) is None


@pytest.mark.django_db(transaction=True)
async def test_a_parent_named_on_a_thread_with_history_keeps_the_history() -> None:
    server = _server()
    runner = AGUIHeadlessRunner(server)
    user = authenticated_user()
    first = await runner.run_to_first_stop("helper", "one", user, timeout=5)

    await runner.run_to_first_stop(
        "helper", "two", user, timeout=5, thread_id=first.thread_id, parent=_PARENT
    )

    request = server.headless_request(user)
    conversation = await DefaultConversationStore().load(first.thread_id, request=request)
    assert [m["content"] for m in conversation.messages] == ["one", "answer 1", "two", "answer 2"]
    assert conversation.parent == _PARENT


@pytest.mark.django_db(transaction=True)
async def test_a_lineage_named_without_a_parent_is_saved() -> None:
    server = _server(inbound_queue=DefaultInboundQueue())
    user = authenticated_user()

    stop = await AGUIHeadlessRunner(server).run_to_first_stop(
        "helper", "one", user, timeout=5, lineage=_LINEAGE
    )

    request = server.headless_request(user)
    conversation = await DefaultConversationStore().load(stop.thread_id, request=request)
    assert (conversation.parent, conversation.lineage) == (None, _LINEAGE)


async def _run_started(server: AGUIServer, user: Any, run_id: str) -> dict[str, Any]:
    log = await DefaultRunStore().read(run_id, request=server.headless_request(user))
    return next(entry.event for entry in log if entry.event.get("type") == "RUN_STARTED")


class _LateRunner:
    """Lets a mount's toolset name the runner that is built from the mount."""

    runner: AGUIHeadlessRunner

    async def start(self, *args: Any, **options: Any) -> Any:
        return await self.runner.start(*args, **options)


class _Delegates(_Model):
    """Starts one child on ``helper``, then answers once the tool has returned."""

    def build(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            parts = [p for m in messages for p in getattr(m, "parts", [])]
            if any(getattr(p, "part_kind", "") == "tool-return" for p in parts):
                yield "started"
            else:
                args = '{"agent": "helper", "prompt": "look it up"}'
                yield {0: DeltaToolCall(name="start_session", json_args=args, tool_call_id="c1")}

        return FunctionModel(stream_function=stream_fn)


def _planner(late: _LateRunner, queue: DefaultInboundQueue) -> AGUIServer:
    toolset = ChildSessionsToolset(
        "planner",
        agents={"helper": "Looks things up."},
        runner=late,
        index=DefaultChildSessionIndex(DefaultRunStore()),
        queue=queue,
        limits=ChildSessionLimits(timeout=5),
    )
    return _server(
        _Delegates(),
        id="planner",
        toolsets=[toolset],
        inbound_queue=queue,
    )


@pytest.mark.django_db(transaction=True)
async def test_a_child_started_by_a_parent_run_names_that_run_on_its_first_run_started() -> None:
    queue, late = DefaultInboundQueue(), _LateRunner()
    planner, helper = _planner(late, queue), _server(inbound_queue=queue)
    late.runner = AGUIHeadlessRunner(planner, helper)
    user = authenticated_user()

    parent = await late.runner.run_to_first_stop("planner", "plan it", user, timeout=5)
    index = DefaultChildSessionIndex(DefaultRunStore())
    here = ThreadRef(parent.thread_id, "planner")
    [child] = await index.children(here, request=planner.headless_request(user))
    await _settled(helper, child.thread.thread_id)
    state = await DefaultRunStore().get(
        child.thread.thread_id, request=helper.headless_request(user)
    )

    started = await _run_started(helper, user, state.last_run_id)
    assert started["parentRunId"] == parent.run_id


@pytest.mark.django_db(transaction=True)
async def test_a_firing_names_its_routine_on_its_thread_and_later_runs_keep_it() -> None:
    server = _server()
    runner = AGUIHeadlessRunner(server)
    user = authenticated_user()

    first = await runner.run_to_first_stop("helper", "one", user, timeout=5, routine_id="7")
    await runner.run_to_first_stop("helper", "two", user, timeout=5, thread_id=first.thread_id)

    request = server.headless_request(user)
    conversation = await DefaultConversationStore().load(first.thread_id, request=request)
    assert [m["content"] for m in conversation.messages] == ["one", "answer 1", "two", "answer 2"]
    assert (conversation.routine_id, conversation.parent) == ("7", None)
