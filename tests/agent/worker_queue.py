from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from django_service_specs import Unrestricted, adispatch, check_call
from django_spec_agents.headless.run_headless import run_headless
from django_spec_agents.headless.types.firing_state import FiringState
from django_spec_agents.headless.types.headless_runner import HeadlessRunner


class WorkerQueue:
    """A FiringQueue that checks a call as it is enqueued, and whose ``work`` runs it."""

    def __init__(self, runner: HeadlessRunner) -> None:
        self._spec = run_headless(runner, permissions=[Unrestricted()])
        self.enqueued: list[tuple[str, Mapping[str, Any], Any]] = []
        self.results: dict[str, Any] = {}
        self._states: dict[str, FiringState] = {}

    def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
        check_call(self._spec, principal=actor, arguments=params)
        firing_id = uuid.uuid4().hex
        self.enqueued.append((spec_name, params, actor))
        self._states[firing_id] = FiringState.PENDING
        return firing_id

    def state(self, run_id: str) -> FiringState | None:
        return self._states.get(run_id)

    async def work(self) -> Sequence[Any]:
        done = []
        for (_, params, actor), firing_id in zip(self.enqueued, list(self._states), strict=True):
            if self._states[firing_id] is FiringState.PENDING:
                result = await adispatch(self._spec, principal=actor, arguments=params)
                self.results[firing_id] = result
                self._states[firing_id] = FiringState.FINISHED
                done.append(result)
        return done
