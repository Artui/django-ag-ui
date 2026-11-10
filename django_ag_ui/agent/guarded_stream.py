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
    """Yield ``stream`` through; on client-disconnect cancellation, tear down and observe."""
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
        except (TimeoutError, asyncio.CancelledError):
            _logger.warning(
                "django-ag-ui: finalizing a cancelled run did not settle within "
                "%ss; it continues in the background",
                _FINALIZE_TIMEOUT_SECONDS,
            )
        raise cancellation


async def _close_quietly(iterator: AsyncIterator[Any], what: str) -> None:
    """Close ``iterator`` if it can be closed, reporting a failure rather than raising it."""
    try:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()
    except (Exception, asyncio.CancelledError):
        _logger.exception("django-ag-ui: error while closing a cancelled run's %s", what)


async def _finalize_quietly(on_cancel: Callable[[], Awaitable[None]]) -> None:
    """Run ``on_cancel``, reporting whatever it costs rather than losing it."""
    try:
        await on_cancel()
    except (Exception, asyncio.CancelledError):
        _logger.exception(
            "django-ag-ui: error while finalizing a cancelled run",
        )


__all__ = ["guarded_stream"]
