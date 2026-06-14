from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest

from django_ag_ui.agent.guarded_stream import guarded_stream


class _Recorder:
    """Shared spy state for a guard scenario."""

    def __init__(self) -> None:
        self.native_closed = False
        self.cancel_calls = 0

    async def native(self) -> AsyncIterator[str]:
        try:
            while True:
                yield "native-event"
        finally:
            self.native_closed = True

    async def on_cancel(self) -> None:
        self.cancel_calls += 1


async def _chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


async def _blocking_after(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value
    await asyncio.Event().wait()  # park forever; only cancellation ends this


async def test_normal_completion_does_not_invoke_on_cancel() -> None:
    recorder = _Recorder()
    stream = guarded_stream(
        _chunks("a", "b"),
        native_events=recorder.native(),
        on_cancel=recorder.on_cancel,
    )
    assert [chunk async for chunk in stream] == ["a", "b"]
    assert recorder.cancel_calls == 0


async def test_aclose_closes_native_runs_on_cancel_and_reraises_generator_exit() -> None:
    recorder = _Recorder()
    native = recorder.native()
    stream = guarded_stream(
        _blocking_after("a"),
        native_events=native,
        on_cancel=recorder.on_cancel,
    )
    assert await anext(stream) == "a"
    # Start the native generator so its try/finally is live, mirroring a real
    # run where the agent has begun streaming.
    assert await anext(native) == "native-event"

    await stream.aclose()

    assert recorder.native_closed is True
    assert recorder.cancel_calls == 1


async def test_task_cancellation_reraises_and_runs_on_cancel() -> None:
    recorder = _Recorder()
    stream = guarded_stream(
        _blocking_after("a"),
        native_events=recorder.native(),
        on_cancel=recorder.on_cancel,
    )

    async def _consume() -> None:
        async for _chunk in stream:
            pass

    task = asyncio.ensure_future(_consume())
    await asyncio.sleep(0)  # let the consumer reach the blocking await
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorder.cancel_calls == 1


async def test_inner_error_propagates_without_on_cancel() -> None:
    recorder = _Recorder()

    async def _broken() -> AsyncIterator[str]:
        yield "a"
        raise ValueError("boom")

    stream = guarded_stream(
        _broken(),
        native_events=recorder.native(),
        on_cancel=recorder.on_cancel,
    )
    with pytest.raises(ValueError, match="boom"):
        _ = [chunk async for chunk in stream]
    assert recorder.cancel_calls == 0


async def test_on_cancel_failure_is_logged_and_cancellation_still_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _failing_cancel() -> None:
        raise RuntimeError("store down")

    recorder = _Recorder()
    stream = guarded_stream(
        _blocking_after("a"),
        native_events=recorder.native(),
        on_cancel=_failing_cancel,
    )
    assert await anext(stream) == "a"

    with caplog.at_level(logging.ERROR, logger="django_ag_ui.agent"):
        await stream.aclose()

    assert "finalizing a cancelled run" in caplog.text
    # aclose() returning cleanly proves GeneratorExit was re-raised (a
    # swallowed GeneratorExit would make aclose raise RuntimeError).


async def test_native_stream_without_aclose_is_tolerated() -> None:
    class _PlainAsyncIterator:
        def __aiter__(self) -> _PlainAsyncIterator:
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

    calls: list[str] = []

    async def _on_cancel() -> None:
        calls.append("cancelled")

    stream = guarded_stream(
        _blocking_after("a"),
        native_events=_PlainAsyncIterator(),
        on_cancel=_on_cancel,
    )
    assert await anext(stream) == "a"
    await stream.aclose()
    assert calls == ["cancelled"]
