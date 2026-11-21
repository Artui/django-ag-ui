from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from ag_ui.core import (
    AssistantMessage,
    BaseEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    UserMessage,
)
from django_spec_agents.persistence.types.run_claim import RunClaim
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from pydantic_ai import CancellationToken

import django_ag_ui.agent.run_owner as run_owner_module
from django_ag_ui.agent.run_owner import RunOwner


class _Store:
    """A run store double with ``DefaultRunStore``'s answers, recording what it was told."""

    def __init__(self, *, keeps: bool = True, cancel_requested: bool = False) -> None:
        self.logged: list[dict[str, Any]] = []
        self.finished: list[RunOutcome] = []
        self.keeps = keeps
        self.cancel_requested = cancel_requested
        self.refuse_append = False
        self.finish_raises = False
        self.append_raises = False

    async def claim(self, thread_id: str, run_id: str, *, request: Any, lease: Any) -> RunClaim:
        return RunClaim(claimed=True, active_run_id=run_id)

    async def append(self, run_id: str, events: list[Any], *, request: Any) -> int | None:
        if self.refuse_append:
            return None
        if self.append_raises:
            raise RuntimeError("store down")
        self.logged.extend(events)
        return len(self.logged)

    async def renew(self, thread_id: str, run_id: str, *, request: Any, lease: Any) -> bool:
        return self.keeps

    async def get(self, thread_id: str, *, request: Any) -> Any:
        return SimpleNamespace(cancel_requested=self.cancel_requested)

    async def finish(
        self,
        thread_id: str,
        run_id: str,
        outcome: RunOutcome,
        *,
        request: Any,
        waiting: Any = None,
    ) -> bool:
        if self.finish_raises:
            raise RuntimeError("store down")
        self.finished.append(outcome)
        return True


def _text(delta: str, message_id: str = "m1") -> TextMessageContentEvent:
    return TextMessageContentEvent(message_id=message_id, delta=delta)


async def _events(*events: BaseEvent, park: bool = False) -> AsyncIterator[BaseEvent]:
    for event in events:
        yield event
    if park:
        await asyncio.Event().wait()


def _owner(
    events: AsyncIterator[BaseEvent],
    *,
    store: _Store | None = None,
    closed: list[str] | None = None,
    token: CancellationToken | None = None,
    ended: RunOutcome | None = None,
    racing: AsyncIterator[Any] | None = None,
    run_input: RunAgentInput | None = None,
) -> RunOwner:
    closed = closed if closed is not None else []

    async def native() -> AsyncIterator[Any]:
        try:
            yield None
            await asyncio.Event().wait()
        finally:
            closed.append("provider stream")

    async def on_cancel() -> None:
        closed.append("finalized")

    return RunOwner(
        events,
        native_events=native(),
        racing_stages=() if racing is None else (racing,),
        on_cancel=on_cancel,
        ended=lambda: ended,
        token=token if token is not None else CancellationToken(),
        run_store=store,  # ty: ignore[invalid-argument-type]
        request=SimpleNamespace(),  # ty: ignore[invalid-argument-type]
        thread_id="t1",
        run_id="r1",
        run_input=run_input,
    )


async def test_a_cancel_closes_the_racing_stage_then_the_provider_then_finalizes() -> None:
    closed: list[str] = []

    async def racing() -> AsyncIterator[Any]:
        try:
            yield None
            await asyncio.Event().wait()
        finally:
            closed.append("racing stage")

    stage = racing()
    await anext(stage)
    store = _Store()
    owner = _owner(_events(_text("hi"), park=True), store=store, closed=closed, racing=stage)
    native = owner._native
    owner.start()
    await anext(native)
    await asyncio.sleep(0.01)

    await owner.cancel()

    assert closed == ["racing stage", "provider stream", "finalized"]
    assert store.finished == [RunOutcome.CANCELLED]


async def test_a_cancel_before_the_task_runs_still_finalizes() -> None:
    closed: list[str] = []
    store = _Store()
    owner = _owner(_events(park=True), store=store, closed=closed)

    await owner.cancel()

    assert "finalized" in closed
    assert store.finished == [RunOutcome.CANCELLED]


async def test_consecutive_deltas_of_one_message_log_as_one() -> None:
    store = _Store()
    owner = _owner(
        _events(
            RunStartedEvent(thread_id="t1", run_id="r1"),
            _text("a"),
            _text("b"),
            _text("c", message_id="m2"),
            RunFinishedEvent(thread_id="t1", run_id="r1"),
        ),
        store=store,
    )

    assert await owner.wait() is RunOutcome.COMPLETED

    assert [(e["type"], e.get("delta")) for e in store.logged] == [
        ("RUN_STARTED", None),
        ("TEXT_MESSAGE_CONTENT", "ab"),
        ("TEXT_MESSAGE_CONTENT", "c"),
        ("RUN_FINISHED", None),
    ]
    assert store.finished == [RunOutcome.COMPLETED]


async def test_an_owner_given_no_run_input_logs_a_run_started_as_it_came() -> None:
    store = _Store()
    echoed = RunAgentInput(
        thread_id="t1", run_id="r1", state=None, messages=[], tools=[], context=[]
    )
    started = RunStartedEvent(thread_id="t1", run_id="r1", input=echoed)

    await _owner(_events(started), store=store).wait()

    assert store.logged[0]["input"] == {
        "threadId": "t1",
        "runId": "r1",
        "messages": [],
        "tools": [],
        "context": [],
    }


async def test_only_the_logged_run_start_carries_the_new_turn() -> None:
    store = _Store()
    posted = [
        UserMessage(id="u1", content="before"),
        AssistantMessage(id="a1", content="answered"),
        UserMessage(id="u2", content="now"),
    ]
    run_input = RunAgentInput(
        thread_id="t1", run_id="r1", state={"x": 1}, messages=posted, tools=[], context=[]
    )
    started = RunStartedEvent(thread_id="t1", run_id="r1")

    await _owner(_events(started, _text("a")), store=store, run_input=run_input).wait()

    assert [m["id"] for m in store.logged[0]["input"]["messages"]] == ["u2"]
    assert "state" not in store.logged[0]["input"]
    assert "input" not in store.logged[1]
    assert RunStartedEvent.model_validate(store.logged[0]).input is not None


async def test_a_delta_held_past_the_window_is_flushed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_owner_module, "_DELTA_FLUSH_SECONDS", 0.0)
    store = _Store()

    await _owner(_events(_text("a"), _text("b"), _text("c")), store=store).wait()

    assert [e["delta"] for e in store.logged] == ["ab", "c"]


async def test_a_run_error_ends_the_run_as_an_error() -> None:
    store = _Store()

    outcome = await _owner(_events(RunErrorEvent(message="boom")), store=store).wait()

    assert outcome is RunOutcome.ERROR
    assert store.finished == [RunOutcome.ERROR]


async def test_how_the_session_saw_it_end_names_the_outcome() -> None:
    owner = _owner(_events(), store=_Store(), ended=RunOutcome.TIMED_OUT)

    assert await owner.wait() is RunOutcome.TIMED_OUT


async def test_a_pipeline_that_raises_reaches_the_subscriber_and_ends_in_error() -> None:
    async def broken() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        raise ValueError("boom")

    owner = _owner(broken())

    with pytest.raises(ValueError, match="boom"):
        _ = [event async for event in owner.subscribe()]
    assert await owner.wait() is RunOutcome.ERROR


async def test_a_detached_subscriber_stops_receiving_while_the_run_goes_on() -> None:
    store = _Store()
    release = asyncio.Event()

    async def events() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        await release.wait()
        yield RunFinishedEvent(thread_id="t1", run_id="r1")

    owner = _owner(events(), store=store)
    subscription = owner.subscribe()
    assert (await anext(subscription)).type == "TEXT_MESSAGE_CONTENT"

    await owner.detach()
    release.set()

    assert await owner.wait() is RunOutcome.COMPLETED
    assert store.logged[-1]["type"] == "RUN_FINISHED"


async def test_a_refused_append_trips_the_token(caplog: pytest.LogCaptureFixture) -> None:
    store, token = _Store(), CancellationToken()
    store.refuse_append = True

    with caplog.at_level(logging.WARNING, logger="django_ag_ui.agent"):
        await _owner(
            _events(RunStartedEvent(thread_id="t1", run_id="r1")), store=store, token=token
        ).wait()

    assert token.cancelled
    assert "lost its thread" in caplog.text


@pytest.mark.parametrize(
    "store",
    [_Store(keeps=False), _Store(cancel_requested=True)],
    ids=["lease lost", "cancel recorded"],
)
async def test_the_watcher_trips_the_token(store: _Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_owner_module, "_WATCH_SECONDS", 0.01)
    token = CancellationToken()
    owner = _owner(_events(park=True), store=store, token=token)
    owner.start()

    for _ in range(100):
        if token.cancelled:
            break
        await asyncio.sleep(0.01)

    assert token.cancelled
    await owner.cancel()


async def test_the_watcher_leaves_a_live_run_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_owner_module, "_WATCH_SECONDS", 0.01)
    token = CancellationToken()
    owner = _owner(_events(park=True), store=_Store(), token=token)
    owner.start()

    await asyncio.sleep(0.05)

    assert not token.cancelled
    await owner.cancel()


async def test_a_failing_finish_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    store = _Store()
    store.finish_raises = True

    with caplog.at_level(logging.ERROR, logger="django_ag_ui.agent"):
        assert await _owner(_events(), store=store).wait() is RunOutcome.COMPLETED

    assert "recording how a run ended" in caplog.text


async def test_without_a_store_nothing_is_logged_and_the_run_still_ends() -> None:
    owner = _owner(_events(_text("a")))

    assert [event.type for event in [e async for e in owner.subscribe()]] == [
        "TEXT_MESSAGE_CONTENT"
    ]
    assert await owner.wait() is RunOutcome.COMPLETED


async def test_a_stage_that_cannot_be_closed_is_passed_over() -> None:
    class _Plain:
        def __aiter__(self) -> _Plain:
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

    closed: list[str] = []
    owner = _owner(_events(park=True), closed=closed, racing=_Plain())
    owner.start()
    await anext(owner._native)

    await owner.cancel()

    assert closed == ["provider stream", "finalized"]


async def test_a_cancel_mid_message_logs_the_held_delta() -> None:
    store = _Store()
    owner = _owner(_events(_text("a"), park=True), store=store)
    owner.start()
    await asyncio.sleep(0.01)

    await owner.cancel()

    assert [e.get("delta") for e in store.logged] == ["a"]
    assert store.finished == [RunOutcome.CANCELLED]


async def test_a_pipeline_that_raises_mid_message_logs_the_held_delta() -> None:
    async def broken() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        raise ValueError("boom")

    store = _Store()

    assert await _owner(broken(), store=store).wait() is RunOutcome.ERROR
    assert [e.get("delta") for e in store.logged] == ["a"]


async def test_an_append_that_raises_closes_the_stages_and_finalizes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    closed: list[str] = []
    store = _Store()
    store.append_raises = True
    owner = _owner(
        _events(RunStartedEvent(thread_id="t1", run_id="r1")), store=store, closed=closed
    )
    await anext(owner._native)

    with caplog.at_level(logging.ERROR, logger="django_ag_ui.agent"):
        assert await owner.wait() is RunOutcome.ERROR

    assert closed == ["provider stream", "finalized"]
    assert store.finished == [RunOutcome.ERROR]


async def test_a_held_delta_that_cannot_be_logged_is_passed_over(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _Store()
    owner = _owner(_events(_text("a"), park=True), store=store)
    owner.start()
    await asyncio.sleep(0.01)
    store.append_raises = True

    with caplog.at_level(logging.ERROR, logger="django_ag_ui.agent"):
        await owner.cancel()

    assert "logging a run's held text" in caplog.text
    assert store.finished == [RunOutcome.CANCELLED]


@pytest.mark.parametrize(
    "terminal",
    [RunFinishedEvent(thread_id="t1", run_id="r1"), RunErrorEvent(message="boom")],
    ids=["finished", "errored"],
)
async def test_an_append_that_raises_after_the_end_does_not_finalize_again(
    terminal: BaseEvent, caplog: pytest.LogCaptureFixture
) -> None:
    closed: list[str] = []
    store = _Store()
    store.append_raises = True
    owner = _owner(_events(terminal), store=store, closed=closed)
    await anext(owner._native)

    with caplog.at_level(logging.ERROR, logger="django_ag_ui.agent"):
        assert await owner.wait() is RunOutcome.ERROR

    assert closed == ["provider stream"]
