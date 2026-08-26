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
    """Partition a step-store **factory** by a scope name.

    A step ledger is keyed by ``(owner_id, run_id)``. Two AG-UI endpoints handed
    the same ``step_store`` therefore share one user's runs: a run recorded at
    ``/internal/agent`` is listed by ``/public/agent/runs/`` and, because
    ``resume/<run_id>/`` addresses a run by id, can be continued there — under
    the *public* agent's model, tools and guard policy. Owner scoping does not
    catch it: it is the same user on both mounts.

    Wrapping fixes that without a migration:

        internal = AGUIServer(
            registry,
            step_store=ScopedStepStore(DefaultStepStore, scope="internal"),
            config=build_ag_ui_config(tool_guard=ToolGuardConfig(enabled=True)),
        )
        public = AGUIServer(
            registry,
            step_store=ScopedStepStore(DefaultStepStore, scope="public"),
        )

    **A factory in, a factory out**, unlike
    [`ScopedConversationStore`][django_ag_ui.ScopedConversationStore], which
    wraps a store: the harness protocol's methods carry no request, so
    ``step_store=`` takes a ``request -> StepStore`` callable and the store is
    built per call. This is that callable, and calling it wraps whatever the
    inner factory returned.

    The partition is a **run-id prefix**, so it composes with any implementation,
    third-party ones included, where a ``scope`` column would mean a migration
    and a breaking change to a protocol that is upstream's, not ours. A run
    belonging to another scope is not *refused* on ``resume/`` — it is simply not
    found, so a probe cannot confirm the id exists either.

    The scope is invisible on the wire. Run ids are handed back to the client
    unchanged; only the storage key carries the prefix.

    **Opt in explicitly.** A transport does not wrap by itself: doing so from its
    namespace would orphan every run an existing single-endpoint project had
    already recorded, the moment it set one. For the same reason, adding a scope
    to a mount that has been running hides that mount's earlier runs from
    ``runs/`` rather than migrating them.
    """

    def __init__(self, inner: StepStoreFactory, *, scope: str) -> None:
        self._inner = inner
        self._scope = scope

    def __call__(self, request: HttpRequest) -> Any:
        # ``Any`` rather than the private wrapper class: what comes back is a
        # structural ``StepStore``, and that protocol is upstream's and optional,
        # so this package types the whole seam ``Any`` -- as ``RunsView`` does
        # for the factory it is handed.
        return _ScopedStore(self._inner(request), scope=self._scope)


class _ScopedStore:
    """One request's scoped view of a step store.

    Every method translates on the way in and back on the way out, so the caller
    never sees a storage key and the inner store never sees a bare run id.
    ``parent_run_id`` is deliberately **not** translated: it is only ever a run id
    this wrapper already handed out, so it is un-prefixed on both sides of every
    call and stays consistent without a second translation to keep in step.
    """

    def __init__(self, inner: Any, *, scope: str) -> None:
        self._inner = inner
        self._scope = scope
        self._prefix = f"{scope}:"

    def _key(self, run_id: str) -> str:
        return f"{self._prefix}{run_id}"

    def _unkey(self, run_id: str) -> str:
        if run_id.startswith(self._prefix):
            return run_id[len(self._prefix) :]
        # A row written before this mount was scoped. Handed back as it is rather
        # than mangled -- it is already unreachable through this wrapper, and a
        # truncated id would be worse than an honest one.
        return run_id

    def _mine(self, run_id: str) -> bool:
        return run_id.startswith(self._prefix)

    def _scoped_in(self, record: Any) -> Any:
        return dataclasses.replace(record, run_id=self._key(record.run_id))

    def _scoped_out(self, record: Any) -> Any:
        return dataclasses.replace(record, run_id=self._unkey(record.run_id))

    # -- Runs -----------------------------------------------------------------

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
        """This scope's runs only, still oldest-first, with keys translated back.

        The inner store answers for the owner across every scope, so the filter
        happens here. That means a busy sibling scope cannot hide rows the way it
        can with a limit applied upstream: the protocol offers no limit to apply.
        """
        records = await self._inner.list_runs(
            parent_run_id=parent_run_id, conversation_id=conversation_id
        )
        return [self._scoped_out(record) for record in records if self._mine(record.run_id)]

    # -- Events ---------------------------------------------------------------

    async def append_event(self, event: Any) -> None:
        await self._inner.append_event(self._scoped_in(event))

    async def list_events(self, *, run_id: str) -> list[Any]:
        events = await self._inner.list_events(run_id=self._key(run_id))
        return [self._scoped_out(event) for event in events]

    # -- Snapshots ------------------------------------------------------------

    async def save_snapshot(self, snapshot: Any) -> None:
        await self._inner.save_snapshot(self._scoped_in(snapshot))

    async def latest_snapshot(self, *, run_id: str, include_interrupted: bool = False) -> Any:
        """The run's last checkpoint, or ``None`` when this scope has no such run.

        The load-bearing method: ``resume/<run_id>/`` and ``fork/<run_id>/`` seed
        a new run from whatever this returns, so a run id belonging to another
        endpoint answers ``None`` here and the resume finds nothing to continue.
        """
        snapshot = await self._inner.latest_snapshot(
            run_id=self._key(run_id), include_interrupted=include_interrupted
        )
        return None if snapshot is None else self._scoped_out(snapshot)

    # -- Tool effects ---------------------------------------------------------

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
