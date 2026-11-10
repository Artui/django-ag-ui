from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from ag_ui.core import BaseEvent, CustomEvent, EventType, TextMessageStartEvent

from django_ag_ui.agent.inject_subagent_events import inject_subagent_events
from django_ag_ui.agent.subagent_lifecycle import subagent_lifecycle
from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK
from django_ag_ui.agent.subagent_progress import subagent_progress

_TIMEOUT = 5.0
"""Generous enough never to fire on a loaded machine, short enough that a
regression to burst-at-the-end reports a failure rather than a hung suite."""


def _text(message_id: str) -> BaseEvent:
    return TextMessageStartEvent(message_id=message_id)


def _step(marker: str = "calling search_docs") -> None:
    """Queue a step event the way the observer does, from inside the stream."""
    sink = SUBAGENT_SINK.get()
    assert sink is not None
    sink.put_nowait(
        subagent_progress(
            delegation_id="call-1",
            agent="researcher",
            phase="tool_call",
            status=marker,
            tool_call_id="sub-1",
            tool_name="search_docs",
        )
    )


def _close() -> None:
    """Queue the delegation's own closing event, the way the observer does."""
    sink = SUBAGENT_SINK.get()
    assert sink is not None
    sink.put_nowait(
        subagent_lifecycle(delegation_id="call-1", agent="researcher", phase="finished")
    )


async def _collect(stream: AsyncIterator[BaseEvent]) -> list[BaseEvent]:
    return await asyncio.wait_for(_drain(stream), _TIMEOUT)


async def _drain(stream: AsyncIterator[BaseEvent]) -> list[BaseEvent]:
    return [event async for event in stream]


async def test_a_stream_with_no_delegation_is_unchanged() -> None:
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        yield _text("b")

    out = await _collect(inject_subagent_events(upstream()))

    assert [event.type for event in out] == [EventType.TEXT_MESSAGE_START] * 2


async def test_progress_ships_while_upstream_is_still_blocked() -> None:
    # The feature as a deadlock: upstream waits for the consumer to see the progress first.
    released = asyncio.Event()

    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        _step()
        await released.wait()
        yield _text("b")

    async def consume() -> list[BaseEvent]:
        seen: list[BaseEvent] = []
        async for event in inject_subagent_events(upstream()):
            seen.append(event)
            if isinstance(event, CustomEvent):
                released.set()
        return seen

    out = await asyncio.wait_for(consume(), _TIMEOUT)

    assert [event.type for event in out] == [
        EventType.TEXT_MESSAGE_START,
        EventType.CUSTOM,
        EventType.TEXT_MESSAGE_START,
    ]


async def test_progress_announced_after_the_last_event_still_ships() -> None:
    # A delegation's completion is announced from inside the last call; without flush it is lost.
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        _step("second call returned")
        _close()

    out = await _collect(inject_subagent_events(upstream()))

    # One from each carrier: the flush is a drain, indifferent to what it drains.
    assert [event.type for event in out] == [
        EventType.TEXT_MESSAGE_START,
        EventType.CUSTOM,
        EventType.SUBAGENT_FINISHED,
    ]


async def test_an_upstream_failure_reaches_the_consumer() -> None:
    class _Boom(Exception): ...

    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        raise _Boom

    # Queued behind the preceding events, not surfacing as a task nobody awaited.
    with pytest.raises(_Boom):
        await _collect(inject_subagent_events(upstream()))


async def test_the_sink_is_reset_when_the_stream_ends() -> None:
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")

    await _collect(inject_subagent_events(upstream()))

    assert SUBAGENT_SINK.get() is None


async def test_the_sink_is_reset_when_the_stream_raises() -> None:
    class _Boom(Exception): ...

    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        raise _Boom

    with contextlib.suppress(_Boom):
        await _collect(inject_subagent_events(upstream()))

    # A run that errors mid-stream must not leave the sink bound to the next run's queue.
    assert SUBAGENT_SINK.get() is None


def _stalling_upstream(closed: asyncio.Event) -> AsyncIterator[BaseEvent]:
    """One event, then a wait nothing ever releases -- a run still in progress."""

    async def upstream() -> AsyncIterator[BaseEvent]:
        try:
            yield _text("a")
            await asyncio.Event().wait()  # pragma: no cover - never released
        finally:
            closed.set()

    return upstream()


async def test_a_consumer_that_stops_early_tears_the_pump_down() -> None:
    # The disconnect path.
    closed = asyncio.Event()

    async def abort() -> Any:
        stream = inject_subagent_events(_stalling_upstream(closed))
        assert isinstance(await stream.__anext__(), TextMessageStartEvent)
        await stream.aclose()
        return SUBAGENT_SINK.get()

    sink_after = await asyncio.wait_for(abort(), _TIMEOUT)

    assert closed.is_set()
    assert sink_after is None


async def test_an_upstream_with_nothing_to_close_is_fine() -> None:
    # The pump closes upstream on its way out, and the parameter is an
    # AsyncIterator -- which a hand-written iterator satisfies without being an
    # async generator, so there is nothing to close.
    class _Iterator:
        def __init__(self) -> None:
            self._left = [_text("a")]

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> BaseEvent:
            if not self._left:
                raise StopAsyncIteration
            return self._left.pop()

    out = await _collect(inject_subagent_events(_Iterator()))

    assert [event.type for event in out] == [EventType.TEXT_MESSAGE_START]


async def test_closing_from_another_task_is_survivable() -> None:
    # The finalizer runs aclose in a foreign context, and ContextVar.reset refuses that token.
    closed = asyncio.Event()
    stream = inject_subagent_events(_stalling_upstream(closed))
    assert isinstance(await stream.__anext__(), TextMessageStartEvent)

    await asyncio.wait_for(stream.aclose(), _TIMEOUT)

    assert closed.is_set()


async def test_concurrent_runs_do_not_share_a_channel() -> None:
    # A ContextVar, not long-lived state, so two runs in flight cannot interleave transcripts.
    async def run(name: str) -> list[Any]:
        async def upstream() -> AsyncIterator[BaseEvent]:
            yield _text(name)
            _step(f"from-{name}")

        return [
            event.value["status"]
            for event in await _collect(inject_subagent_events(upstream()))
            if isinstance(event, CustomEvent)
        ]

    first, second = await asyncio.gather(run("a"), run("b"))

    assert first == ["from-a"]
    assert second == ["from-b"]
