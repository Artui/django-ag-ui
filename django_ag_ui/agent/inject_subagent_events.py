from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import BaseEvent

from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK
from django_ag_ui.agent.utils import STREAM_END, discard_tasks, pump_in_lockstep


async def inject_subagent_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, interleaving each announced event **as it is announced**."""
    progress: asyncio.Queue[BaseEvent] = asyncio.Queue()
    token = SUBAGENT_SINK.set(progress)
    events: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(stream, events))
    next_event: asyncio.Task[Any] | None = None
    next_progress: asyncio.Task[BaseEvent] | None = None
    ended = False
    try:
        while True:
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
            events.task_done()
    finally:
        with contextlib.suppress(ValueError):
            SUBAGENT_SINK.reset(token)
        await discard_tasks(pump, next_event, next_progress)


__all__ = ["inject_subagent_events"]
