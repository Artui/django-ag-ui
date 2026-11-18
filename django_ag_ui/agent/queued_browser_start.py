from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django_spec_agents.headless.types.firing_queue import FiringQueue
from django_spec_agents.headless.types.headless_firing import HeadlessFiring
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_store import RunStore

from django_ag_ui.agent.thread_busy_error import ThreadBusyError
from django_ag_ui.agent.types.stored_browser_input import StoredBrowserInput
from django_ag_ui.agent.utils import is_queued_input
from django_ag_ui.constants import QUEUED_INPUT_EVENT, QUEUED_RUN_LEASE, RUN_HEADLESS_SPEC


class QueuedBrowserStart:
    """A detached browser start a worker runs: its thread claimed, its input kept, then enqueued.

    The input is the run log's first entry, a CUSTOM ``ag_ui.queued_input`` event the
    events replay skips, so the worker reads it back by ``run_id`` from the same store.
    """

    def __init__(
        self,
        queue: FiringQueue,
        runs: RunStore,
        *,
        agent_id: str,
        timeout: float,
        spec_name: str = RUN_HEADLESS_SPEC,
        lease: timedelta = QUEUED_RUN_LEASE,
    ) -> None:
        self._queue = queue
        self._runs = runs
        self._agent_id = agent_id
        self._timeout = timeout
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
        claim = await self._runs.claim(thread_id, run_id, request=request, lease=self._lease)
        if not claim.claimed:
            raise ThreadBusyError(thread_id, claim.active_run_id)
        try:
            await self._runs.append(run_id, [_record(stored)], request=request)
            firing = HeadlessFiring(
                self._agent_id, "", self._timeout, thread_id, None, None, run_id, None
            )
            params = dataclasses.asdict(firing)
            await sync_to_async(self._queue.enqueue)(self._spec_name, params, actor=request.user)
        except BaseException:
            await self._runs.finish(thread_id, run_id, RunOutcome.ERROR, request=request)
            raise

    @staticmethod
    async def stored(
        runs: RunStore, run_id: str, request: HttpRequest
    ) -> StoredBrowserInput | None:
        """The input a queued browser start kept for ``run_id``; ``None`` for any other run."""
        entries = await runs.read(run_id, request=request, limit=1)
        if not entries or not is_queued_input(entries[0].event):
            return None
        value = entries[0].event["value"]
        return StoredBrowserInput(body=value["input"], resume_from=value["resume_from"])


def _record(stored: StoredBrowserInput) -> dict[str, Any]:
    value = {"input": stored.body, "resume_from": stored.resume_from}
    return {"type": "CUSTOM", "name": QUEUED_INPUT_EVENT, "value": value}


__all__ = ["QueuedBrowserStart"]
