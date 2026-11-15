from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest
from django_spec_agents import IdentifierTooLongError

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

    ``check_identifiers`` is forwarded, with the prefix applied to ``run_id``, only
    when the inner store has it, so ``preflight_identifiers`` skips a store without
    one. A ``run_id`` refused there is reported at the length the client sent,
    against the room the prefix leaves.

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
        # ``Any`` rather than the private wrapper: the protocol is optional and upstream's.
        return _ScopedStore(self._inner(request), scope=self._scope)


class _ScopedStore:
    """One request's scoped view of a step store."""

    def __init__(self, inner: Any, *, scope: str) -> None:
        self._inner = inner
        self._scope = scope
        self._prefix = f"{scope}:"
        # Offered only over a store that has it, so a scoped custom store never looks capable.
        if callable(getattr(inner, "check_identifiers", None)):
            self.check_identifiers = self._check_identifiers

    def _check_identifiers(self, **ids: str | None) -> None:
        run_id = ids.get("run_id")
        if run_id is not None:
            ids["run_id"] = self._key(run_id)
        try:
            self._inner.check_identifiers(**ids)
        except IdentifierTooLongError as error:
            raise self._as_sent(error, run_id) from error

    def _as_sent(self, error: IdentifierTooLongError, run_id: str | None) -> IdentifierTooLongError:
        """``error`` counted against the id the client sent, when it is about ``run_id``."""
        if run_id is None or error.field != "run_id" or error.limit is None:
            return error
        limit = error.limit - len(self._prefix)
        return IdentifierTooLongError(
            f"run_id is {len(run_id)} characters, over its {limit}-character limit in this scope",
            field="run_id",
            length=len(run_id),
            limit=limit,
        )

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
