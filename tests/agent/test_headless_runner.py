from __future__ import annotations

import asyncio
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import AgentInfo, FunctionModel

import django_ag_ui.agent.run_owner as run_owner_module
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.agent.thread_busy_error import ThreadBusyError
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
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
