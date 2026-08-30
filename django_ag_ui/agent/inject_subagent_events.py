"""``inject_subagent_events`` -- surface a delegation's progress on the stream."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import BaseEvent, CustomEvent

from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK

_END = object()
"""Queued by the pump to say the upstream stream is exhausted.

A sentinel rather than letting ``StopAsyncIteration`` out of the pump: raised
inside a task it is not the loop-ending signal it is inside an ``async for``,
and asyncio turns it into a bare ``RuntimeError`` on the way through.
"""


async def inject_subagent_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, interleaving each progress event **as it is announced**.

    Establishes the per-run channel [`SubAgentObserver`][django_ag_ui.SubAgentObserver]
    announces onto, then races it against the upstream stream so a queued event
    goes out immediately rather than waiting for upstream to produce something.

    **The race is the feature, not an optimisation.** The two sibling injectors
    drain a plain list between upstream events, which is enough for what they
    carry: a compaction happens during a model request and an invalidation during
    a write, and either way another event follows shortly. A delegation is the
    opposite shape -- the child's entire run happens inside one ``delegate_task``
    tool call, and the AG-UI stream is silent between that call's
    ``TOOL_CALL_END`` and its ``TOOL_CALL_RESULT``. Drained the sibling way,
    every progress event a five-minute delegation produced would arrive in one
    burst *after* it finished, which describes the stall rather than fixing it.

    **Upstream is pumped by one long-lived task, and its lifetime is the reason.**
    Racing means awaiting upstream as a future, and a future is a task, and a
    task runs in a *copy* of the context. One task per ``__anext__`` would
    therefore hand the upstream generators a different context on every step --
    which breaks them, because that is precisely where the sibling injectors set
    and reset their own sinks, and where the tools that write into those sinks
    run. One task for the whole stream keeps the upstream chain in a single
    context from first event to last, exactly as it was before anything raced.

    **The pump is kept in lockstep** -- it asks upstream for the next event only
    once this generator has yielded the previous one, which is exactly when a
    plain ``async for`` would have asked. That preserves the client's
    backpressure (a browser that stops reading stops the run), and it is also
    what makes the interleaving *true*: a pump allowed to run even one event
    ahead would let progress announced while producing event N+1 overtake event
    N, and the client would see a delegation start before the tool call that
    started it.

    Applied unconditionally, for the reason the siblings give: it is inert unless
    something announces during the run, and a flag would mean a second way to
    express the opt-in that wrapping the capability already is.

    Anything still queued when upstream ends is flushed before this returns --
    the last thing a delegation announces is its own completion, and upstream may
    have no event left to carry it out on.
    """
    progress: asyncio.Queue[CustomEvent] = asyncio.Queue()
    token = SUBAGENT_SINK.set(progress)
    events: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(_pump(stream, events))
    next_event: asyncio.Task[Any] | None = None
    next_progress: asyncio.Task[CustomEvent] | None = None
    ended = False
    try:
        while True:
            # The single drain, and the reason it is at the top: under lockstep
            # anything queued here was announced while upstream was producing the
            # event that has not been taken yet, so it goes out first. Draining
            # before the end check is also what stops a delegation's closing
            # words being dropped when the run finishes in the same breath.
            while not progress.empty():
                yield progress.get_nowait()
            if ended:
                break
            if next_event is None:
                next_event = asyncio.ensure_future(events.get())
            if next_progress is None:
                next_progress = asyncio.ensure_future(progress.get())
            done, _ = await asyncio.wait(
                {next_event, next_progress}, return_when=asyncio.FIRST_COMPLETED
            )
            if next_progress in done:
                # Progress wins a tie, for the same reason the drain comes first.
                # The event's task keeps its result and is taken on a later turn.
                yield next_progress.result()
                next_progress = None
                continue
            item = next_event.result()
            next_event = None
            if item is _END:
                ended = True
                continue
            if isinstance(item, BaseException):
                raise item
            yield item
            # After the yield, never before: this is the signal the pump waits on
            # before asking upstream for anything more.
            events.task_done()
    finally:
        # ``reset`` refuses a token raised in another context, which is exactly
        # what an ``aclose`` driven from a different task is -- the event loop's
        # own async-generator finalizer, or a caller that wrapped the close in
        # ``wait_for``. Letting that go leaks nothing: the binding it would have
        # cleared belongs to a context that is itself ending.
        with contextlib.suppress(ValueError):
            SUBAGENT_SINK.reset(token)
        # Awaited, not merely cancelled. The pump holds the upstream chain, and
        # returning while that is still unwinding leaves the generator "already
        # running" for whoever closes it next -- which on the disconnect path is
        # ``guarded_stream``, one frame out, closing the provider's stream.
        await _discard(pump, next_event, next_progress)


async def _pump(stream: AsyncIterator[BaseEvent], out: asyncio.Queue[Any]) -> None:
    """Move ``stream`` into ``out`` one event at a time, then say how it ended.

    ``join`` is what makes it lockstep: the next ``__anext__`` waits for the
    consumer's ``task_done``, so upstream advances no sooner than it would have
    under a plain ``async for``.

    An exception is queued rather than raised so it reaches the consumer in
    order, behind the events that preceded it, instead of surfacing out of band
    as a task nobody awaited.

    Upstream is closed on the way out, and that is what a pump owes back. A
    cancelled pump is a consumer that has gone away, and its cancellation lands
    on ``join`` as often as inside ``__anext__`` -- which leaves the upstream
    generator suspended at its own yield with its ``finally`` unrun, waiting on
    garbage collection. Closing it here is the same obligation ``guarded_stream``
    discharges one frame further out for the provider's own stream.
    """
    try:
        async for event in stream:
            await out.put(event)
            await out.join()
        await out.put(_END)
    except Exception as error:
        await out.put(error)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()


async def _discard(*tasks: asyncio.Future[Any] | None) -> None:
    """Cancel whichever of ``tasks`` are still live, and wait for them to settle."""
    live = [task for task in tasks if task is not None]
    for task in live:
        task.cancel()
    await asyncio.gather(*live, return_exceptions=True)


__all__ = ["inject_subagent_events"]
