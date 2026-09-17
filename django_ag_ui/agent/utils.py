"""Shared machinery for the stream wrappers that have to act while upstream is blocked."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

STREAM_END = object()
"""Queued by the pump to say the upstream stream is exhausted.

A sentinel rather than letting ``StopAsyncIteration`` out of the pump: raised
inside a task it is not the loop-ending signal it is inside an ``async for``,
and asyncio turns it into a bare ``RuntimeError`` on the way through.
"""


async def pump_in_lockstep(stream: AsyncIterator[Any], out: asyncio.Queue[Any]) -> None:
    """Move ``stream`` into ``out`` one item at a time, then say how it ended.

    The half of a racing stream wrapper that is not the generator. A wrapper that
    has to emit something *while* upstream is silent has to await upstream as a
    future, and a future is a task -- so upstream is driven from here and the
    wrapper selects between this queue and whatever else it is watching.

    **One long-lived task, not one per ``__anext__``, and that is load-bearing.**
    A task runs in a *copy* of the context, so a task per step would hand the
    upstream generators a different context on every item -- which breaks them,
    because that is precisely where
    [`inject_subagent_events`][django_ag_ui.agent.inject_subagent_events.inject_subagent_events]
    sets and resets its own sink, and where the tools that write into that sink
    run. One task for the whole stream keeps the upstream chain in a single
    context from first item to last, exactly as it was before anything raced.

    ``join`` is what makes it lockstep: the next ``__anext__`` waits for the
    consumer's ``task_done``, so upstream advances no sooner than it would have
    under a plain ``async for``. That preserves the client's backpressure (a
    browser that stops reading stops the run), and it keeps any interleaving
    *true*: a pump allowed to run even one item ahead would let something
    produced while upstream built item N+1 overtake item N.

    An exception is queued rather than raised so it reaches the consumer in
    order, behind the items that preceded it, instead of surfacing out of band
    as a task nobody awaited.

    Upstream is closed on the way out, and that is what a pump owes back. A
    cancelled pump is a consumer that has gone away, and its cancellation lands
    on ``join`` as often as inside ``__anext__`` -- which leaves the upstream
    generator suspended at its own yield with its ``finally`` unrun, waiting on
    garbage collection. Closing it here is the same obligation ``guarded_stream``
    discharges further out for the provider's own stream.
    """
    try:
        async for item in stream:
            await out.put(item)
            await out.join()
        await out.put(STREAM_END)
    except Exception as error:
        await out.put(error)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()


async def discard_tasks(*tasks: asyncio.Future[Any] | None) -> None:
    """Cancel whichever of ``tasks`` are still live, and wait for them to settle.

    Awaited, not merely cancelled. A pump holds the upstream chain, and returning
    while that is still unwinding leaves the generator "already running" for
    whoever closes it next -- which on the disconnect path is ``guarded_stream``,
    one frame out, closing the provider's stream.
    """
    live = [task for task in tasks if task is not None]
    for task in live:
        task.cancel()
    await asyncio.gather(*live, return_exceptions=True)


__all__ = ["STREAM_END", "discard_tasks", "pump_in_lockstep"]
