from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
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
    racing_stages: Sequence[AsyncIterator[Any]] = (),
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

    **``racing_stages`` are closed first, outermost first, and after ``stream``
    itself.** A racing stage drives its upstream from a task of its own so it can
    write while upstream is blocked, which means the frames it writes reach the
    client exactly while that task is inside the provider's stream. A
    ``GeneratorExit`` delivered at such a frame finds ``native_events`` running,
    and closing it raises "already running" instead of stopping the generation.
    Closing the stage cancels its task and waits for it, and only then is the
    provider's stream closable. Outermost first because a stage nested inside
    another's task is itself running until the outer one lets go.

    Only the racing stages are listed, not every generator in the chain. The rest
    are not holding anything open, and several of them reset a context variable
    on the way out: closed from this task rather than the one that iterated them,
    that reset raises.

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
        for stage in (stream, *racing_stages):
            await _close_quietly(stage, "stream stage")
        await _close_quietly(native_events, "provider stream")
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


async def _close_quietly(iterator: AsyncIterator[Any], what: str) -> None:
    """Close ``iterator`` if it can be closed, reporting a failure rather than raising it.

    Each close has its own handler, so one that blows up neither stops the next
    nor costs the run its record: closing the chain and observing what the run
    did are separate obligations.
    """
    try:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()
    except (Exception, asyncio.CancelledError):
        _logger.exception("django-ag-ui: error while closing a cancelled run's %s", what)


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
