"""``inject_subagent_events`` -- surface a delegation's progress on the stream."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import BaseEvent

from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK
from django_ag_ui.agent.utils import STREAM_END, discard_tasks, pump_in_lockstep


async def inject_subagent_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, interleaving each announced event **as it is announced**.

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

    **Upstream is pumped by one long-lived task, kept in lockstep** --
    [`pump_in_lockstep`][django_ag_ui.agent.utils.pump_in_lockstep], which is
    where both halves of that are argued. The short of it: racing means awaiting
    upstream as a future, one future per step would hand the upstream generators
    a fresh context copy each time and break the very sinks the siblings set, and
    a pump allowed to run an event ahead would let progress announced while
    producing event N+1 overtake event N -- so the client would see a delegation
    start before the tool call that started it.

    Applied unconditionally, for the reason the siblings give: it is inert unless
    something announces during the run, and a flag would mean a second way to
    express the opt-in that wrapping the capability already is.

    Anything still queued when upstream ends is flushed before this returns --
    the last thing a delegation announces is its own completion, and upstream may
    have no event left to carry it out on.
    """
    progress: asyncio.Queue[BaseEvent] = asyncio.Queue()
    token = SUBAGENT_SINK.set(progress)
    events: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(stream, events))
    next_event: asyncio.Task[Any] | None = None
    next_progress: asyncio.Task[BaseEvent] | None = None
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
            if item is STREAM_END:
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
        await discard_tasks(pump, next_event, next_progress)


__all__ = ["inject_subagent_events"]
