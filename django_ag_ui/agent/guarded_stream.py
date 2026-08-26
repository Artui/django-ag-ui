from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

_logger = logging.getLogger("django_ag_ui.agent")

# How long a disconnected request waits for its own finalisation before letting
# go of it. Aborting a stream is one cheap request for a client and a
# conversation save plus an audit write for the server, so an unbounded wait
# turns repeated aborts into workers parked in teardown behind a slow or
# contended store. Past the bound the write is not cancelled — it is shielded
# and left to land on its own; only the wait ends.
_FINALIZE_TIMEOUT_SECONDS = 5.0


async def guarded_stream(
    stream: AsyncIterator[str],
    *,
    native_events: AsyncIterator[Any],
    on_cancel: Callable[[], Awaitable[None]],
) -> AsyncIterator[str]:
    """Yield ``stream`` through; on client-disconnect cancellation, tear down and observe.

    AG-UI has no server-side cancel route — the client aborts the streaming
    request, and under Django's ASGI handler the disconnect surfaces here in one
    of two shapes:

    - ``asyncio.CancelledError`` — the handler cancels the consuming task and the
      error is delivered at the innermost ``await``, unwinding the agent run on
      its way up through this frame.
    - ``GeneratorExit`` — this generator is ``aclose()``d directly (the loop's
      async-generator finalizer, test harnesses), leaving the inner generators
      suspended at their own yields, never seeing the exception.

    Either way the guard closes ``native_events``, the innermost generator, whose
    agent-run context manager owns the provider's streaming request — so upstream
    teardown is guaranteed rather than left to garbage-collection order, and an
    orphaned generation stops billing. On the ``CancelledError`` path the chain
    has already unwound and the ``aclose()`` is a no-op.

    ``on_cancel`` then persists / audits the cancelled run, **shielded and time
    bounded**. Shielded because this already runs inside the cancellation: a
    second one delivered mid-write would otherwise abort the store call and take
    the audit record with it. Bounded because the wait happens in the
    disconnected request's own task — see ``_FINALIZE_TIMEOUT_SECONDS``.
    Failures are logged and swallowed so the cancellation itself is always
    re-raised: swallowing ``CancelledError`` breaks the caller's teardown
    contract.
    """
    try:
        async for chunk in stream:
            yield chunk
    except (asyncio.CancelledError, GeneratorExit) as cancellation:
        try:
            aclose = getattr(native_events, "aclose", None)
            if aclose is not None:
                await aclose()
        except (Exception, asyncio.CancelledError):
            # Its own handler, so a teardown that blows up does not also cost
            # the run its record: closing the provider's stream and observing
            # what the run did are two separate obligations.
            _logger.exception(
                "django-ag-ui: error while closing a cancelled run's provider stream",
            )
        finalize = asyncio.ensure_future(_finalize_quietly(on_cancel))
        try:
            await asyncio.wait_for(asyncio.shield(finalize), _FINALIZE_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _logger.warning(
                "django-ag-ui: finalizing a cancelled run did not settle within "
                "%ss; it continues in the background",
                _FINALIZE_TIMEOUT_SECONDS,
            )
        raise cancellation


async def _finalize_quietly(on_cancel: Callable[[], Awaitable[None]]) -> None:
    """Run ``on_cancel``, reporting whatever it costs rather than losing it.

    ``asyncio.CancelledError`` is caught alongside ``Exception`` deliberately:
    it has inherited from ``BaseException`` since Python 3.8, so the plain
    ``except Exception`` this replaced never saw a store torn down mid-write —
    the partial conversation and the cancellation audit record both vanished
    with nothing logged, leaving the run reading as neither completed nor
    cancelled.

    Swallowing here is also what makes the detached case safe: past the wait
    bound nobody is left to read this task's outcome, and a task that cannot
    raise cannot strand an unretrieved exception.
    """
    try:
        await on_cancel()
    except (Exception, asyncio.CancelledError):
        _logger.exception(
            "django-ag-ui: error while finalizing a cancelled run",
        )


__all__ = ["guarded_stream"]
