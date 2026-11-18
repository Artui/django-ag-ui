from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_spec_agents.headless.types.firing_queue import FiringQueue
from django_spec_agents.headless.types.headless_firing import HeadlessFiring
from django_spec_agents.persistence.types.run_store import RunStore

from django_ag_ui.agent.thread_busy_error import ThreadBusyError
from django_ag_ui.agent.types.stored_browser_input import StoredBrowserInput
from django_ag_ui.agent.utils import claim_thread, give_back, kept_for_worker, queued_record
from django_ag_ui.constants import QUEUED_RUN_LEASE, RUN_HEADLESS_SPEC


class QueuedBrowserStart:
    """A detached browser start a worker runs: its thread claimed, its input kept, enqueued.

    The input is the run log's first entry, which the events replay skips."""

    def __init__(
        self,
        queue: FiringQueue,
        runs: RunStore,
        *,
        agent_id: str,
        timeout: float,
        inbox_mark: Callable[[HttpRequest, str], Awaitable[int | None]],
        spec_name: str = RUN_HEADLESS_SPEC,
        lease: timedelta = QUEUED_RUN_LEASE,
    ) -> None:
        self._queue = queue
        self._runs = runs
        self._agent_id = agent_id
        self._timeout = timeout
        self._inbox_mark = inbox_mark
        self._spec_name = spec_name
        self._lease = lease

    async def enqueue(
        self,
        request: HttpRequest,
        thread_id: str,
        run_id: str,
        stored: StoredBrowserInput,
    ) -> None:
        """Claim the thread for ``run_id``, keep ``stored`` and enqueue it; busy raises."""
        mark = await self._inbox_mark(request, thread_id)
        resumes = stored.resume_from if stored.resume_parked else None
        claim = await claim_thread(
            self._runs, thread_id, run_id, request=request, lease=self._lease, resumes=resumes
        )
        if not claim.claimed:
            raise ThreadBusyError(thread_id, claim.active_run_id)
        try:
            await self._runs.append(run_id, [_record(stored, mark)], request=request)
            firing = HeadlessFiring(
                self._agent_id, "", self._timeout, thread_id, run_id=run_id, inbox_mark=mark
            )
            params = dataclasses.asdict(firing)
            await sync_to_async(self._queue.enqueue)(self._spec_name, params, actor=request.user)
        except BaseException:
            await give_back(self._runs, thread_id, run_id, request=request)
            raise

    @staticmethod
    async def stored(
        runs: RunStore, run_id: str, request: HttpRequest
    ) -> StoredBrowserInput | None:
        """The input a queued browser start kept for ``run_id``; ``None`` for any other run."""
        value = await kept_for_worker(runs, run_id, request)
        if value is None or "input" not in value:
            return None
        return StoredBrowserInput(value["input"], value["resume_from"], value["resume_parked"])


def _record(stored: StoredBrowserInput, mark: int | None) -> dict[str, Any]:
    value = {
        "input": stored.body,
        "resume_from": stored.resume_from,
        "resume_parked": stored.resume_parked,
        "inbox_mark": mark,
    }
    return queued_record(value)


__all__ = ["QueuedBrowserStart"]
