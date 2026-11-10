from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from django_ag_ui.agent.utils import STREAM_END, discard_tasks, pump_in_lockstep

_HEARTBEAT_COMMENT = ": django-ag-ui heartbeat\n\n"
"""The frame written into a silent stream.

An SSE **comment**: the protocol says a line beginning with a colon is ignored,
so every conformant ``EventSource`` -- and the ``ag-ui`` clients built on one --
drops it before any event handler sees it. That is the whole reason a comment is
the right shape here rather than a custom event: it puts bytes on the wire
without putting anything in the protocol, so no client needs to be taught about
it and none can mistake it for an AG-UI event.

Named rather than bare (``:\\n\\n`` would also be legal) because the first person
to find these in a packet capture or an access log should not have to guess who
emitted them.
"""


async def heartbeat_stream(stream: AsyncIterator[str], *, interval: float) -> AsyncIterator[str]:
    """Forward ``stream``, emitting a comment frame whenever it goes quiet for ``interval``."""
    chunks: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(stream, chunks))
    next_chunk: asyncio.Task[Any] | None = None
    try:
        while True:
            if next_chunk is None:
                next_chunk = asyncio.ensure_future(chunks.get())
            done, _ = await asyncio.wait({next_chunk}, timeout=interval)
            if not done:
                yield _HEARTBEAT_COMMENT
                continue
            item = next_chunk.result()
            next_chunk = None
            if item is STREAM_END:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
            chunks.task_done()
    finally:
        await discard_tasks(pump, next_chunk)


__all__ = ["heartbeat_stream"]
