from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from ag_ui.core import BaseEvent, RunErrorEvent, RunFinishedEvent, TextMessageContentEvent
from django.http import HttpRequest
from django_spec_agents.persistence.types.run_claim import RunClaim
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_store import RunStore
from pydantic_ai import CancellationToken

from django_ag_ui.agent.utils import STREAM_END, discard_tasks
from django_ag_ui.constants import RUN_LEASE

_logger = logging.getLogger("django_ag_ui.agent")

# A lease is renewed three times over, so one slow write never lapses it.
_WATCH_SECONDS = RUN_LEASE.total_seconds() / 3
_DELTA_FLUSH_SECONDS = 0.5


class RunOwner:
    """Runs a run's pipeline in its own task; a ``run_store`` adds claim, log and cancel."""

    def __init__(
        self,
        events: AsyncIterator[BaseEvent],
        *,
        native_events: AsyncIterator[Any],
        racing_stages: Sequence[AsyncIterator[Any]] = (),
        on_cancel: Callable[[], Awaitable[None]],
        ended: Callable[[], RunOutcome | None],
        token: CancellationToken,
        run_store: RunStore | None = None,
        request: HttpRequest | None = None,
        thread_id: str = "",
        run_id: str = "",
    ) -> None:
        self._events = events
        self._native = native_events
        self._racing = tuple(racing_stages)
        self._on_cancel = on_cancel
        self._ended = ended
        self._token = token
        self._store = run_store
        self._request = request
        self._thread_id = thread_id
        self._run_id = run_id
        self._live: asyncio.Queue[Any] | None = asyncio.Queue()
        self._task: asyncio.Task[RunOutcome] | None = None
        self._pending: TextMessageContentEvent | None = None
        self._pending_since = 0.0
        self._running = False

    async def claim(self) -> RunClaim:
        """Take the thread for this run; a loss names the run that holds it."""
        assert self._store is not None and self._request is not None
        return await self._store.claim(
            self._thread_id, self._run_id, request=self._request, lease=RUN_LEASE
        )

    def start(self) -> asyncio.Task[RunOutcome]:
        """Start the run's task, once; the caller keeps a reference to it."""
        if self._task is None:
            self._task = asyncio.ensure_future(self._own())
        return self._task

    async def wait(self) -> RunOutcome:
        """How the run ended, once it has."""
        return await asyncio.shield(self.start())

    async def subscribe(self) -> AsyncIterator[BaseEvent]:
        """The run's events as they happen, for the one subscriber it has."""
        queue = self._live
        assert queue is not None
        self.start()
        while True:
            item = await queue.get()
            if item is STREAM_END:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    async def detach(self) -> None:
        """Let the subscriber go; the run carries on and keeps logging."""
        self._live = None

    async def cancel(self) -> None:
        """End the run now: close its stages, then persist and audit the partial."""
        self._live = None
        task = self.start()
        if not self._running:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _own(self) -> RunOutcome:
        self._running = True
        outcome = RunOutcome.COMPLETED
        ended, end = False, STREAM_END
        watcher = None if self._store is None else asyncio.ensure_future(self._watch())
        try:
            async for event in self._events:
                if isinstance(event, RunErrorEvent):
                    outcome = RunOutcome.ERROR
                ended = ended or isinstance(event, (RunErrorEvent, RunFinishedEvent))
                self._deliver(event)
                await self._log(event)
            ended = True
            await self._log(None)
            outcome = self._ended() or outcome
        except asyncio.CancelledError:
            outcome = RunOutcome.CANCELLED
            raise
        except Exception as error:
            outcome, end = RunOutcome.ERROR, error
        finally:
            await self._wind_down(ended)
            await discard_tasks(watcher)
            await self._finish(outcome)
            # Last, so a subscriber's loop ending (as under WSGI) never cuts the bookkeeping.
            self._deliver(end)
        return outcome

    async def _wind_down(self, ended: bool) -> None:
        """Log the held delta and close the stages; a run cut short before its end is finalized."""
        if self._pending is not None:
            await _quietly(self._log(None), "logging a run's held text")
        for stage in (self._events, *self._racing):
            await _close_quietly(stage, "stream stage")
        await _close_quietly(self._native, "provider stream")
        if not ended:
            await _quietly(self._on_cancel(), "finalizing a cancelled run")

    def _deliver(self, item: Any) -> None:
        if self._live is not None:
            self._live.put_nowait(item)

    async def _log(self, event: BaseEvent | None) -> None:
        """Append ``event``, holding a text delta back to merge with the next of its message."""
        if self._store is None:
            return
        held = self._pending
        if isinstance(event, TextMessageContentEvent):
            if held is not None and held.message_id == event.message_id:
                merged = held.model_copy(update={"delta": held.delta + event.delta})
                self._pending = merged
                if time.monotonic() - self._pending_since >= _DELTA_FLUSH_SECONDS:
                    self._pending = None
                    await self._append([merged])
                return
            self._pending, self._pending_since = event, time.monotonic()
            if held is not None:
                await self._append([held])
            return
        self._pending = None
        batch = [e for e in (held, event) if e is not None]
        if batch:
            await self._append(batch)

    async def _append(self, events: list[BaseEvent]) -> None:
        assert self._store is not None and self._request is not None
        dumped = [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in events]
        appended = await self._store.append(self._run_id, dumped, request=self._request)
        if appended is None:
            _logger.warning("django-ag-ui: run %s lost its thread; cancelling it", self._run_id)
            self._token.cancel()

    async def _watch(self) -> None:
        """Keep the lease, and trip the run's token on a recorded cancel or a lost claim."""
        assert self._store is not None and self._request is not None
        while True:
            await asyncio.sleep(_WATCH_SECONDS)
            kept = await self._store.renew(
                self._thread_id, self._run_id, request=self._request, lease=RUN_LEASE
            )
            state = await self._store.get(self._thread_id, request=self._request)
            if not kept or (state is not None and state.cancel_requested):
                self._token.cancel()
                return

    async def _finish(self, outcome: RunOutcome) -> None:
        if self._store is None:
            return
        assert self._request is not None
        finish = self._store.finish(self._thread_id, self._run_id, outcome, request=self._request)
        await _quietly(finish, "recording how a run ended")


async def _close_quietly(iterator: AsyncIterator[Any], what: str) -> None:
    aclose = getattr(iterator, "aclose", None)
    if aclose is not None:
        await _quietly(aclose(), f"closing a cancelled run's {what}")


async def _quietly(awaitable: Awaitable[Any], what: str) -> None:
    """Await ``awaitable``, logging a failure rather than raising it."""
    try:
        await awaitable
    except (Exception, asyncio.CancelledError):
        _logger.exception("django-ag-ui: error while %s", what)


__all__ = ["RunOwner"]
