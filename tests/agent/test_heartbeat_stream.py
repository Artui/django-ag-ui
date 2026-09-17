"""What the heartbeat puts on the wire, and what a client does with it.

The claim being tested is about **bytes**, not about intent: a proxy severs a
connection it has seen no bytes on, so "we call a heartbeat function" proves
nothing and "these frames appear in the stream, and a conformant client drops
them" proves the whole thing. Hence the miniature ``EventSource`` below, which
implements the one rule the guarantee rests on.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from django_ag_ui.agent.heartbeat_stream import _HEARTBEAT_COMMENT, heartbeat_stream

# Short enough that a silent stream beats several times inside a test, long
# enough that a busy one is never mistaken for a silent one.
_INTERVAL = 0.02


def _event_source_dispatch(raw: str) -> list[tuple[str, str]]:
    """Dispatch ``raw`` the way the SSE spec says an ``EventSource`` must.

    The rules that matter here, from the WHATWG event-stream algorithm: a line
    beginning with a colon is **ignored**, a blank line dispatches the buffered
    event, and an event whose data buffer is empty is not dispatched at all. That
    third rule is why a comment cannot even produce a silent empty event -- it
    never reaches the buffer, so there is nothing to dispatch.

    Written out rather than asserted about because "an EventSource ignores it" is
    the load-bearing claim of the whole design.
    """
    dispatched: list[tuple[str, str]] = []
    name = ""
    data: list[str] = []
    for line in raw.split("\n"):
        if line == "":
            if data:
                dispatched.append((name or "message", "\n".join(data)))
            name, data = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            name = value
        elif field == "data":
            data.append(value)
    return dispatched


async def _until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    """Wait for ``predicate``, rather than sleeping a guessed number of intervals."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("the stream never reached the expected state")
        await asyncio.sleep(_INTERVAL / 4)


async def _gated(before: str, after: str, gate: asyncio.Event) -> AsyncIterator[str]:
    """One frame, then silence until ``gate`` opens, then one more."""
    yield before
    await gate.wait()
    yield after


async def _closeable(first: str) -> AsyncIterator[str]:
    """One frame, then silence forever. Sets nothing; the caller owns the event."""
    yield first
    await asyncio.Event().wait()


async def test_a_silent_stream_carries_comment_frames_until_it_speaks_again() -> None:
    gate = asyncio.Event()
    stream = heartbeat_stream(_gated("data: one\n\n", "data: two\n\n", gate), interval=_INTERVAL)

    seen: list[str] = []

    async def _consume() -> None:
        async for chunk in stream:
            seen.append(chunk)

    consumer = asyncio.ensure_future(_consume())
    # More than one beat: a single frame could be a coincidence of scheduling,
    # several is the interval doing its job for as long as the silence lasts.
    await _until(lambda: seen.count(_HEARTBEAT_COMMENT) >= 3)
    gate.set()
    await consumer

    assert seen[0] == "data: one\n\n"
    assert seen[-1] == "data: two\n\n"
    # Every beat fell in the silence between the two real frames, and the real
    # frames kept their order.
    assert set(seen[1:-1]) == {_HEARTBEAT_COMMENT}
    assert [chunk for chunk in seen if chunk != _HEARTBEAT_COMMENT] == [
        "data: one\n\n",
        "data: two\n\n",
    ]


async def test_the_frame_is_an_sse_comment_an_event_source_drops() -> None:
    gate = asyncio.Event()
    stream = heartbeat_stream(
        _gated("event: RUN_STARTED\ndata: {}\n\n", "event: RUN_FINISHED\ndata: {}\n\n", gate),
        interval=_INTERVAL,
    )

    seen: list[str] = []

    async def _consume() -> None:
        async for chunk in stream:
            seen.append(chunk)

    consumer = asyncio.ensure_future(_consume())
    await _until(lambda: _HEARTBEAT_COMMENT in seen)
    gate.set()
    await consumer

    assert _HEARTBEAT_COMMENT.startswith(":")
    # The bytes a proxy counts are on the wire...
    wire = "".join(seen)
    assert _HEARTBEAT_COMMENT in wire
    # ...and the events a client sees are exactly the two the run emitted.
    assert _event_source_dispatch(wire) == [("RUN_STARTED", "{}"), ("RUN_FINISHED", "{}")]


async def test_a_busy_stream_is_not_padded() -> None:
    """The clock is the wait for the next chunk, so it restarts on every frame.
    A stream that never goes quiet for a whole interval gets no beats at all."""

    async def _chatty() -> AsyncIterator[str]:
        for index in range(20):
            await asyncio.sleep(_INTERVAL / 10)
            yield f"data: {index}\n\n"

    seen = [chunk async for chunk in heartbeat_stream(_chatty(), interval=_INTERVAL)]

    assert _HEARTBEAT_COMMENT not in seen
    assert seen == [f"data: {index}\n\n" for index in range(20)]


async def test_the_beat_stops_when_the_run_ends() -> None:
    """A finished run's response is closed, and a beat outliving it would be a
    task writing into a socket nobody owns."""
    gate = asyncio.Event()
    before = asyncio.all_tasks()
    stream = heartbeat_stream(_gated("data: one\n\n", "data: two\n\n", gate), interval=_INTERVAL)

    seen: list[str] = []

    async def _consume() -> None:
        async for chunk in stream:
            seen.append(chunk)

    consumer = asyncio.ensure_future(_consume())
    await _until(lambda: seen.count(_HEARTBEAT_COMMENT) >= 2)
    gate.set()
    await consumer

    # The run is over. Several intervals later the stream has not grown, and
    # nothing is left running that could grow it.
    ended_with = list(seen)
    await asyncio.sleep(_INTERVAL * 5)
    assert seen == ended_with
    assert seen[-1] == "data: two\n\n"
    assert asyncio.all_tasks() - before == set()


async def test_closing_the_stream_leaves_no_task_behind() -> None:
    """``aclose`` is the disconnect path's shape when the generator is closed
    directly rather than cancelled. A leaked pump per aborted run is the failure
    this has to not have."""
    closed = asyncio.Event()

    async def _watched() -> AsyncIterator[str]:
        try:
            async for chunk in _closeable("a"):
                yield chunk
        finally:
            closed.set()

    before = asyncio.all_tasks()
    stream = heartbeat_stream(_watched(), interval=_INTERVAL)
    assert await anext(stream) == "a"

    await stream.aclose()

    # Upstream was closed rather than left to garbage collection, and the pump
    # that held it is gone.
    assert closed.is_set()
    assert asyncio.all_tasks() - before == set()


async def test_cancelling_the_consumer_leaves_no_task_behind() -> None:
    """The live disconnect path: Django cancels the task consuming the response,
    and the error lands inside this generator's own wait."""
    closed = asyncio.Event()

    async def _watched() -> AsyncIterator[str]:
        try:
            async for chunk in _closeable("a"):
                yield chunk
        finally:
            closed.set()

    before = asyncio.all_tasks()
    stream = heartbeat_stream(_watched(), interval=_INTERVAL)
    seen: list[str] = []

    async def _consume() -> None:
        async for chunk in stream:
            seen.append(chunk)

    consumer = asyncio.ensure_future(_consume())
    # Past the first beat, so the cancellation lands while the wrapper is parked
    # in its own wait rather than anywhere upstream.
    await _until(lambda: _HEARTBEAT_COMMENT in seen)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert closed.is_set()
    assert asyncio.all_tasks() - before == set()


async def test_an_upstream_exception_reaches_the_consumer() -> None:
    """Queued by the pump rather than raised in it, so it has to be re-raised
    here or a failing run would end as a clean close."""

    async def _raises() -> AsyncIterator[str]:
        yield "a"
        raise RuntimeError("upstream blew up")

    stream = heartbeat_stream(_raises(), interval=_INTERVAL)
    assert await anext(stream) == "a"
    with pytest.raises(RuntimeError, match="upstream blew up"):
        await anext(stream)


async def test_backpressure_survives_the_heartbeat() -> None:
    """A consumer that stops reading still stops the run. The wrapper races a
    clock, not the producer -- upstream is asked for the next frame only once the
    previous one has been handed on, exactly as a plain ``async for`` would."""
    produced: list[int] = []

    async def _counted() -> AsyncIterator[str]:
        for index in range(5):
            produced.append(index)
            yield f"data: {index}\n\n"

    stream = heartbeat_stream(_counted(), interval=_INTERVAL)
    assert await anext(stream) == "data: 0\n\n"

    # Sit on the first frame for several intervals. The run does not advance:
    # nothing is produced speculatively to fill a buffer.
    await asyncio.sleep(_INTERVAL * 5)
    assert produced == [0]

    assert await anext(stream) == "data: 1\n\n"
    await stream.aclose()
