from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest

# The step-store protocol and its records live in ``pydantic-ai-harness``, which
# arrives with the ``[harness]`` extra. Typed ``Any`` so this module imports
# without it — the package core stays installable on a bare install, exactly as
# ``RunsView`` does.
StepStoreFactory = Callable[[HttpRequest], Any]


class ScopedStepStore:
    """Partition a step-store **factory** by a scope name."""

    def __init__(self, inner: StepStoreFactory, *, scope: str) -> None:
        self._inner = inner
        self._scope = scope

    def __call__(self, request: HttpRequest) -> Any:
        # ``Any`` rather than the private wrapper: the protocol is optional and upstream's.
        return _ScopedStore(self._inner(request), scope=self._scope)


class _ScopedStore:
    """One request's scoped view of a step store."""

    def __init__(self, inner: Any, *, scope: str) -> None:
        self._inner = inner
        self._scope = scope
        self._prefix = f"{scope}:"

    def _key(self, run_id: str) -> str:
        return f"{self._prefix}{run_id}"

    def _unkey(self, run_id: str) -> str:
        if run_id.startswith(self._prefix):
            return run_id[len(self._prefix) :]
        # A row written before this mount was scoped.
        return run_id

    def _mine(self, run_id: str) -> bool:
        return run_id.startswith(self._prefix)

    def _scoped_in(self, record: Any) -> Any:
        return dataclasses.replace(record, run_id=self._key(record.run_id))

    def _scoped_out(self, record: Any) -> Any:
        return dataclasses.replace(record, run_id=self._unkey(record.run_id))

    # Runs

    async def register_run(self, record: Any) -> None:
        await self._inner.register_run(self._scoped_in(record))

    async def get_run(self, *, run_id: str) -> Any:
        record = await self._inner.get_run(run_id=self._key(run_id))
        return None if record is None else self._scoped_out(record)

    async def list_runs(
        self,
        *,
        parent_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[Any]:
        """This scope's runs only, still oldest-first, with keys translated back."""
        records = await self._inner.list_runs(
            parent_run_id=parent_run_id, conversation_id=conversation_id
        )
        return [self._scoped_out(record) for record in records if self._mine(record.run_id)]

    # Events

    async def append_event(self, event: Any) -> None:
        await self._inner.append_event(self._scoped_in(event))

    async def list_events(self, *, run_id: str) -> list[Any]:
        events = await self._inner.list_events(run_id=self._key(run_id))
        return [self._scoped_out(event) for event in events]

    async def save_snapshot(self, snapshot: Any) -> None:
        await self._inner.save_snapshot(self._scoped_in(snapshot))

    async def latest_snapshot(self, *, run_id: str, include_interrupted: bool = False) -> Any:
        """The run's last checkpoint, or ``None`` when this scope has no such run."""
        snapshot = await self._inner.latest_snapshot(
            run_id=self._key(run_id), include_interrupted=include_interrupted
        )
        return None if snapshot is None else self._scoped_out(snapshot)

    async def record_tool_effect(self, record: Any) -> None:
        await self._inner.record_tool_effect(self._scoped_in(record))

    async def get_tool_effect(self, *, run_id: str, tool_call_id: str) -> Any:
        record = await self._inner.get_tool_effect(
            run_id=self._key(run_id), tool_call_id=tool_call_id
        )
        return None if record is None else self._scoped_out(record)

    async def list_unresolved_tool_effects(self, *, run_id: str) -> list[Any]:
        records = await self._inner.list_unresolved_tool_effects(run_id=self._key(run_id))
        return [self._scoped_out(record) for record in records]


__all__ = ["ScopedStepStore"]
