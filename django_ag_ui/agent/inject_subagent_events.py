from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from typing import Any

from ag_ui.core import (
    BaseEvent,
    RunErrorEvent,
    RunFinishedEvent,
    SubagentErrorEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
)

from django_ag_ui.agent.event_timestamp import event_timestamp
from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK, SUBAGENT_TASKS
from django_ag_ui.agent.utils import STREAM_END, discard_tasks, pump_in_lockstep


async def inject_subagent_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, interleaving each announced event **as it is announced**."""
    tracker = _OpenDelegations()
    async with contextlib.aclosing(_interleave(stream)) as interleaved:
        async for event in interleaved:
            if isinstance(event, RunFinishedEvent | RunErrorEvent):
                for close in tracker.close():
                    yield close
                yield event
            elif tracker.see(event):
                yield event


async def _interleave(stream: AsyncIterator[BaseEvent]) -> AsyncGenerator[BaseEvent]:
    """Forward ``stream``, racing each upstream event against the next announced one."""
    progress: asyncio.Queue[BaseEvent] = asyncio.Queue()
    token = SUBAGENT_SINK.set(progress)
    tasks_token = SUBAGENT_TASKS.set({})
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
            SUBAGENT_TASKS.reset(tasks_token)
        with contextlib.suppress(ValueError):
            SUBAGENT_SINK.reset(token)
        await discard_tasks(pump, next_event, next_progress)


class _OpenDelegations:
    """The delegations a stream has opened and not yet closed, by ``subagentRunId``."""

    def __init__(self) -> None:
        self._open: dict[str, None] = {}
        self.finished = False

    def see(self, event: BaseEvent) -> bool:
        """Record ``event``; false when it arrives after the run ended."""
        if self.finished:
            return False
        if isinstance(event, SubagentStartedEvent):
            self._open[event.subagent_run_id] = None
        elif isinstance(event, SubagentFinishedEvent | SubagentErrorEvent):
            self._open.pop(event.subagent_run_id, None)
        return True

    def close(self) -> Iterator[BaseEvent]:
        """An error for each delegation still open, as the run finishes or fails."""
        self.finished = True
        for run_id in self._open:
            yield SubagentErrorEvent(
                subagent_run_id=run_id,
                message="the sub-agent was still running when the run ended",
                code="unfinished",
                timestamp=event_timestamp(),
            )
        self._open.clear()


__all__ = ["inject_subagent_events"]
