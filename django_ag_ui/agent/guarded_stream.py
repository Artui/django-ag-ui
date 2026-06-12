from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


async def guarded_stream(
    stream: AsyncIterator[str],
    *,
    native_events: AsyncIterator[Any],
    on_cancel: Callable[[], Awaitable[None]],
) -> AsyncIterator[str]:
    """Yield ``stream`` through; on client-disconnect cancellation, tear down and observe.

    AG-UI has no server-side cancel route — the client aborts the streaming
    request, and the disconnect surfaces here in one of two shapes (both occur
    under Django's ASGI handler):

    - ``asyncio.CancelledError`` — the handler cancels the task consuming the
      response, and the error is delivered at the innermost ``await`` of the
      streaming chain, unwinding the agent run on its way up through this
      frame.
    - ``GeneratorExit`` — this generator is ``aclose()``d directly (the event
      loop's async-generator finalizer, test harnesses); the inner generators
      are left suspended at their own yields and never see the exception.

    In both cases the guard closes ``native_events`` — the innermost
    generator, whose agent-run context manager owns the provider's streaming
    request — so upstream teardown is guaranteed rather than left to garbage
    collection order (an orphaned generation keeps billing). On the
    ``CancelledError`` path the chain has already unwound and the ``aclose()``
    is a no-op.

    ``on_cancel`` then persists/audits the cancelled run. Its failures are
    logged and swallowed so the cancellation itself is always re-raised:
    swallowing ``CancelledError`` (or replacing it with a persistence error)
    breaks the caller's teardown contract.
    """
    try:
        async for chunk in stream:
            yield chunk
    except (asyncio.CancelledError, GeneratorExit) as cancellation:
        try:
            aclose = getattr(native_events, "aclose", None)
            if aclose is not None:
                await aclose()
            await on_cancel()
        except Exception:
            logging.getLogger("django_ag_ui.agent").exception(
                "django-ag-ui: error while finalizing a cancelled run",
            )
        raise cancellation


__all__ = ["guarded_stream"]
