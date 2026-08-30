"""``inject_subagent_events`` -- putting a delegation's progress on the wire, live.

The property that matters here is *when*, not *what*. A delegated child's whole
run happens inside one ``delegate_task`` tool call, and the AG-UI stream emits
nothing from that call's ``TOOL_CALL_END`` until its ``TOOL_CALL_RESULT`` -- so
an injector that drains between upstream events (which is what the compaction
and invalidation injectors do, correctly, for what they carry) would hold every
progress event until the delegation it describes had already finished. That is
the stall restated, not fixed.

So the load-bearing test below deadlocks rather than merely disagreeing if the
draining ever goes back to being pull-driven, and is bounded by a timeout so a
regression fails instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from ag_ui.core import BaseEvent, CustomEvent, EventType, TextMessageStartEvent

from django_ag_ui.agent.inject_subagent_events import inject_subagent_events
from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK
from django_ag_ui.agent.subagent_progress import subagent_progress

_TIMEOUT = 5.0
"""Generous enough never to fire on a loaded machine, short enough that a
regression to burst-at-the-end reports a failure rather than a hung suite."""


def _text(message_id: str) -> BaseEvent:
    return TextMessageStartEvent(message_id=message_id)


def _announce(phase: Any = "started") -> None:
    """Queue a progress event the way the observer does, from inside the stream."""
    sink = SUBAGENT_SINK.get()
    assert sink is not None
    sink.put_nowait(
        subagent_progress(
            delegation_id="call-1",
            agent="researcher",
            phase=phase,
            status=f"researcher {phase}",
        )
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
    # The whole feature, as a deadlock: upstream refuses to produce its next
    # event until the consumer has already seen the progress announced during
    # this one. Draining between upstream events cannot satisfy that.
    released = asyncio.Event()

    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        _announce("tool_call")
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
    # A delegation's own completion is announced from inside the tool call that
    # produced the run's last events; without the flush it goes on the floor.
    # Two of them, so the flush is asserted to be a drain and not one take.
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        _announce("tool_result")
        _announce("finished")

    out = await _collect(inject_subagent_events(upstream()))

    assert [event.type for event in out] == [
        EventType.TEXT_MESSAGE_START,
        EventType.CUSTOM,
        EventType.CUSTOM,
    ]
    assert [event.value["phase"] for event in out[1:]] == ["tool_result", "finished"]


async def test_an_upstream_failure_reaches_the_consumer() -> None:
    class _Boom(Exception): ...

    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        raise _Boom

    # Queued behind the events that preceded it rather than surfacing out of
    # band as a task nobody awaited.
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

    # A run that errors mid-stream must not leave the sink bound; the next run
    # on this task would otherwise announce into a dead queue.
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
    # The disconnect path. The cancellation lands on the pump while it waits for
    # this generator to take the previous event, *not* inside ``__anext__`` --
    # so without the pump closing upstream on its way out, the whole chain would
    # be left suspended at its own yield with its ``finally`` unrun, and
    # ``guarded_stream`` would be closing the provider's stream one frame out
    # while the generators above it were still open.
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
    # The loop's own async-generator finalizer runs ``aclose`` in a context this
    # generator never set anything in, and ``ContextVar.reset`` refuses a token
    # from elsewhere. Tearing down has to survive that rather than raise into a
    # finalizer nobody is watching.
    closed = asyncio.Event()
    stream = inject_subagent_events(_stalling_upstream(closed))
    assert isinstance(await stream.__anext__(), TextMessageStartEvent)

    await asyncio.wait_for(stream.aclose(), _TIMEOUT)

    assert closed.is_set()


async def test_concurrent_runs_do_not_share_a_channel() -> None:
    # A ContextVar rather than state on anything long-lived: two runs in flight
    # must not interleave into each other's transcripts.
    async def run(name: str) -> list[Any]:
        async def upstream() -> AsyncIterator[BaseEvent]:
            yield _text(name)
            _announce(f"finished-{name}")

        return [
            event.value["phase"]
            for event in await _collect(inject_subagent_events(upstream()))
            if isinstance(event, CustomEvent)
        ]

    first, second = await asyncio.gather(run("a"), run("b"))

    assert first == ["finished-a"]
    assert second == ["finished-b"]
