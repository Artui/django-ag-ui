"""``heartbeat_stream`` -- keep a silent SSE response alive across idle proxies."""

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
    """Forward ``stream``, emitting a comment frame whenever it goes quiet for ``interval``.

    **The failure mode this exists for is a severed stream, and it does not look
    like one.** An idle-timeout proxy closes a connection with no bytes in either
    direction for N seconds; an agent that thinks, or waits on a slow tool call,
    for longer than N emits nothing in that window. The client sees a dead
    connection where an answer was coming, with no error from either end saying
    so -- the run carries on server-side and finishes into a socket nobody is
    reading. Measured against the default that bit us: AWS ALB's
    ``idle_timeout`` is 60s, and so is nginx's ``proxy_read_timeout``.

    **It belongs here rather than in a consumer's infrastructure.** Raising the
    load balancer's timeout does work, and is the wrong shape: ``idle_timeout``
    is an attribute of the balancer, not of the route, so one streaming endpoint
    changes the behaviour of every service sharing it. And the balancer is only
    the proxy a consumer *knows about* -- corporate proxies, mobile carriers and
    CDNs each impose their own idle bound and a consumer controls none of them.
    Bytes on the wire are the only fix that reaches all of them, and this package
    owns the wire.

    **Silence-triggered, not a metronome.** The clock is the wait for the next
    upstream chunk, so it restarts on every real frame: a stream producing
    anything at all inside ``interval`` is never padded, and the guarantee is the
    one that matters to a proxy -- no more than ``interval`` seconds pass with
    nothing on the wire. A timer firing unconditionally would add frames to a
    busy stream to no purpose.

    **Upstream is driven by
    [`pump_in_lockstep`][django_ag_ui.agent.utils.pump_in_lockstep]**, for the
    reasons argued there, plus one specific to waiting on a clock: the pending
    ``__anext__`` is *not* cancelled when the interval expires. ``asyncio.wait``
    with a timeout leaves its futures alone, which ``asyncio.wait_for`` would
    not -- and cancelling an async generator's ``__anext__`` mid-await does not
    merely lose that item, it leaves the generator unusable for the next one. So
    a heartbeat observes the wait rather than interrupting it, and the same task
    is still waiting when the chunk finally lands.

    Ordering is untouched: chunks leave in the order the pump queued them, and a
    comment can only be emitted while nothing is queued.

    Teardown is the pump's. Whether this generator ends normally, is ``aclose``d,
    or takes the run's cancellation at its own ``await``, the ``finally`` cancels
    the pump and waits for it -- and the pump closes upstream on its way out, so
    nothing is left for garbage collection to notice and no task outlives the
    run. ``guarded_stream`` stays the outermost frame and its contract is
    unchanged: on the cancellation path the chain below has already unwound by
    the time it catches.
    """
    chunks: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(stream, chunks))
    next_chunk: asyncio.Task[Any] | None = None
    try:
        while True:
            if next_chunk is None:
                next_chunk = asyncio.ensure_future(chunks.get())
            done, _ = await asyncio.wait({next_chunk}, timeout=interval)
            if not done:
                # Still waiting, and the wait is untouched -- see above. The
                # loop re-enters with the same task and a fresh interval, so a
                # long silence is beaten at ``interval``, not just once.
                yield _HEARTBEAT_COMMENT
                continue
            item = next_chunk.result()
            next_chunk = None
            if item is STREAM_END:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
            # After the yield, never before: this is the signal the pump waits on
            # before asking upstream for anything more.
            chunks.task_done()
    finally:
        await discard_tasks(pump, next_chunk)


__all__ = ["heartbeat_stream"]
