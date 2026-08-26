"""Two endpoints, one step store, no crossing.

The ledger partitions by owner and nothing else, so two mounts handed the same
``step_store`` share one user's runs: ``runs/`` on either mount lists both, and
``resume/<run_id>/`` addresses a run by id, so the *other* endpoint's run
continues under this endpoint's model, tools and guard policy. Owner scoping
cannot catch that -- it is the same user on both mounts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.persistence.scoped_step_store import ScopedStepStore
from tests.authed_request_factory import AuthedRequestFactory

_STARTED = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


class _MemoryStore:
    """An in-memory ledger standing in for the model-backed store.

    Keyed by ``run_id`` alone, which is the shape the wrapper exists for: the
    real store adds an owner column and nothing else.
    """

    def __init__(self) -> None:
        self.runs: list[RunRecord] = []
        self.events: list[StepEvent] = []
        self.snapshots: list[ContinuableSnapshot] = []
        self.effects: list[ToolEffectRecord] = []
        self.interrupted_asked: list[bool] = []

    async def register_run(self, record: RunRecord) -> None:
        self.runs.append(record)

    async def get_run(self, *, run_id: str) -> RunRecord | None:
        return next((r for r in self.runs if r.run_id == run_id), None)

    async def list_runs(
        self, *, parent_run_id: str | None = None, conversation_id: str | None = None
    ) -> list[RunRecord]:
        return [
            record
            for record in self.runs
            if (parent_run_id is None or record.parent_run_id == parent_run_id)
            and (conversation_id is None or record.conversation_id == conversation_id)
        ]

    async def append_event(self, event: StepEvent) -> None:
        self.events.append(event)

    async def list_events(self, *, run_id: str) -> list[StepEvent]:
        return [event for event in self.events if event.run_id == run_id]

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        self.snapshots.append(snapshot)

    async def latest_snapshot(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> ContinuableSnapshot | None:
        self.interrupted_asked.append(include_interrupted)
        found = [s for s in self.snapshots if s.run_id == run_id]
        return found[-1] if found else None

    async def record_tool_effect(self, record: ToolEffectRecord) -> None:
        self.effects.append(record)

    async def get_tool_effect(self, *, run_id: str, tool_call_id: str) -> ToolEffectRecord | None:
        return next(
            (
                effect
                for effect in self.effects
                if effect.run_id == run_id and effect.tool_call_id == tool_call_id
            ),
            None,
        )

    async def list_unresolved_tool_effects(self, *, run_id: str) -> list[ToolEffectRecord]:
        return [effect for effect in self.effects if effect.run_id == run_id]


def _request() -> HttpRequest:
    return RequestFactory().get("/agent/runs/")


def _scoped(inner: _MemoryStore, scope: str) -> Any:
    """One mount's view of the shared ledger, built the way ``AGUIServer`` builds it."""
    return ScopedStepStore(lambda request: inner, scope=scope)(_request())


def _record(run_id: str, *, parent: str | None = None, minutes: int = 0) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id="t1",
        parent_run_id=parent,
        agent_name=None,
        metadata={},
        started_at=_STARTED + timedelta(minutes=minutes),
    )


def _snapshot(run_id: str) -> ContinuableSnapshot:
    return ContinuableSnapshot(
        run_id=run_id,
        step_index=0,
        messages=[],
        conversation_id="t1",
        parent_run_id=None,
        agent_name=None,
        timestamp=_STARTED,
    )


def _effect(run_id: str, *, tool_call_id: str = "c1") -> ToolEffectRecord:
    return ToolEffectRecord(
        tool_call_id=tool_call_id,
        tool_name="do_thing",
        run_id=run_id,
        status="started",
        started_at=_STARTED,
        ended_at=None,
        idempotency_key=None,
        effect_summary=None,
    )


class TestTheBoundaryItKeeps:
    """The finding itself: what one mount records, the other cannot reach."""

    async def test_another_scopes_run_is_not_listed(self) -> None:
        inner = _MemoryStore()
        await _scoped(inner, "internal").register_run(_record("r1"))

        assert await _scoped(inner, "public").list_runs() == []

    async def test_another_scopes_run_has_no_snapshot_to_resume(self) -> None:
        """``resume/<run_id>/`` seeds a run from exactly this call.

        Answering ``None`` is what makes the resume find nothing to continue --
        rather than continuing the internal endpoint's transcript under the
        public endpoint's model, tools and guard policy.
        """
        inner = _MemoryStore()
        internal = _scoped(inner, "internal")
        await internal.register_run(_record("r1"))
        await internal.save_snapshot(_snapshot("r1"))

        assert await _scoped(inner, "public").latest_snapshot(run_id="r1") is None
        assert await internal.latest_snapshot(run_id="r1") is not None

    async def test_another_scopes_run_is_not_found_by_id(self) -> None:
        # Not found rather than refused, so a probe cannot confirm the id exists.
        inner = _MemoryStore()
        await _scoped(inner, "internal").register_run(_record("r1"))

        assert await _scoped(inner, "public").get_run(run_id="r1") is None

    async def test_the_same_run_id_on_two_mounts_stays_two_runs(self) -> None:
        """Ids are generated per run, but nothing stops two ledgers colliding."""
        inner = _MemoryStore()
        await _scoped(inner, "internal").register_run(_record("shared-id"))
        await _scoped(inner, "public").register_run(_record("shared-id"))

        assert len(inner.runs) == 2
        assert {record.run_id for record in inner.runs} == {
            "internal:shared-id",
            "public:shared-id",
        }

    async def test_an_unscoped_store_still_crosses(self) -> None:
        """The control: this is the behaviour without the wrapper."""
        inner = _MemoryStore()
        await inner.register_run(_record("r1"))

        assert [record.run_id for record in await inner.list_runs()] == ["r1"]


class TestTheWireIsUnchanged:
    """The scope is a storage key, never something a client sees or sends."""

    async def test_ids_come_back_the_way_they_went_in(self) -> None:
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.register_run(_record("r1"))

        (listed,) = await store.list_runs()
        assert listed.run_id == "r1"
        assert (await store.get_run(run_id="r1")).run_id == "r1"
        # The prefix exists only in storage.
        assert inner.runs[0].run_id == "internal:r1"

    async def test_a_row_written_before_the_scope_is_handed_back_intact(self) -> None:
        """Wrapping an existing mount hides its earlier runs; it must not mangle them.

        Those rows are already unreachable through the wrapper, so the only
        question is what a caller reading them another way sees -- and a
        truncated id would be worse than an honest one.
        """
        inner = _MemoryStore()
        await inner.register_run(_record("written-before"))

        assert _scoped(inner, "internal")._unkey("written-before") == "written-before"

    async def test_parent_run_id_is_left_alone(self) -> None:
        """A fork's parent is an id this wrapper already handed out.

        It is un-prefixed on both sides of every call, so translating it would be
        a second mapping to keep in step for no gain -- and ``list_runs`` filters
        on the child's key anyway.
        """
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.register_run(_record("child", parent="r1"))

        assert inner.runs[0].parent_run_id == "r1"
        (listed,) = await store.list_runs(parent_run_id="r1")
        assert listed.run_id == "child"


class TestEveryLedgerRow:
    """All four record kinds carry a ``run_id``, so all four are translated."""

    async def test_events_round_trip(self) -> None:
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.append_event(
            StepEvent(
                run_id="r1",
                kind="run_started",
                step_index=0,
                timestamp=_STARTED,
                conversation_id="t1",
                parent_run_id=None,
                agent_name=None,
                tool_call_id=None,
                tool_name=None,
                error=None,
                metadata={},
            )
        )

        assert inner.events[0].run_id == "internal:r1"
        (event,) = await store.list_events(run_id="r1")
        assert event.run_id == "r1"
        assert await _scoped(inner, "public").list_events(run_id="r1") == []

    async def test_snapshots_round_trip(self) -> None:
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.save_snapshot(_snapshot("r1"))

        assert inner.snapshots[0].run_id == "internal:r1"
        assert (await store.latest_snapshot(run_id="r1")).run_id == "r1"

    async def test_the_interrupted_flag_is_forwarded(self) -> None:
        # Not a knob to swallow: the resume path asks for interrupted snapshots
        # when it is continuing an approval.
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.latest_snapshot(run_id="r1", include_interrupted=True)

        assert inner.interrupted_asked == [True]

    async def test_tool_effects_round_trip(self) -> None:
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.record_tool_effect(_effect("r1"))

        assert inner.effects[0].run_id == "internal:r1"
        found = await store.get_tool_effect(run_id="r1", tool_call_id="c1")
        assert found.run_id == "r1"
        assert (
            await _scoped(inner, "public").get_tool_effect(run_id="r1", tool_call_id="c1") is None
        )

    async def test_a_missing_tool_effect_is_none(self) -> None:
        inner = _MemoryStore()
        store = _scoped(inner, "internal")

        assert await store.get_tool_effect(run_id="r1", tool_call_id="nope") is None

    async def test_unresolved_tool_effects_round_trip(self) -> None:
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.record_tool_effect(_effect("r1"))

        (effect,) = await store.list_unresolved_tool_effects(run_id="r1")
        assert effect.run_id == "r1"
        assert await _scoped(inner, "public").list_unresolved_tool_effects(run_id="r1") == []

    async def test_a_missing_run_is_none(self) -> None:
        inner = _MemoryStore()

        assert await _scoped(inner, "internal").get_run(run_id="nope") is None

    async def test_a_missing_snapshot_is_none(self) -> None:
        inner = _MemoryStore()

        assert await _scoped(inner, "internal").latest_snapshot(run_id="nope") is None


class TestTheFactoryShape:
    async def test_the_inner_factory_is_called_per_request(self) -> None:
        """``step_store=`` takes a ``request -> StepStore``, so this must too."""
        seen: list[HttpRequest] = []

        def factory(request: HttpRequest) -> _MemoryStore:
            seen.append(request)
            return _MemoryStore()

        scoped = ScopedStepStore(factory, scope="internal")
        first, second = _request(), _request()
        scoped(first)
        scoped(second)

        assert seen == [first, second]

    async def test_conversation_id_filtering_still_reaches_the_store(self) -> None:
        inner = _MemoryStore()
        store = _scoped(inner, "internal")
        await store.register_run(_record("r1"))

        assert len(await store.list_runs(conversation_id="t1")) == 1
        assert await store.list_runs(conversation_id="other") == []


class TestThroughTheMount:
    """The recipe the finding is about: two endpoints, one shared factory."""

    def _mounts(self, inner: _MemoryStore) -> tuple[Any, Any]:
        """An internal and a public endpoint over one ledger, each scoped."""

        def factory(request: HttpRequest) -> _MemoryStore:
            return inner

        internal = AGUIServer(
            ToolRegistry(),
            model=TestModel(),
            namespace="internal-agent",
            step_store=ScopedStepStore(factory, scope="internal"),
        )
        public = AGUIServer(
            ToolRegistry(),
            model=TestModel(),
            namespace="public-agent",
            step_store=ScopedStepStore(factory, scope="public"),
        )
        return internal, public

    def _runs_view(self, server: AGUIServer) -> Any:
        patterns, _, _ = server.urls
        return next(p for p in patterns if p.name == "runs").callback

    def _step_store(self, server: AGUIServer, request: HttpRequest) -> Any:
        return server._view._step_store(request)

    async def test_the_public_index_does_not_list_the_internal_run(self) -> None:
        inner = _MemoryStore()
        internal, public = self._mounts(inner)
        request = AuthedRequestFactory().get("/internal/agent/runs/")
        await self._step_store(internal, request).register_run(_record("r1"))

        listed = await self._runs_view(public)(AuthedRequestFactory().get("/public/agent/runs/"))
        mine = await self._runs_view(internal)(request)

        assert json.loads(listed.content) == {"runs": []}
        assert [row["run_id"] for row in json.loads(mine.content)["runs"]] == ["r1"]

    async def test_the_public_endpoint_cannot_resume_the_internal_run(self) -> None:
        """``resume/<run_id>/`` reads exactly this, so ``None`` ends the attempt.

        Without the wrapper the same user lists an internal run at the public
        mount and POSTs it back to ``resume/``, continuing that transcript under
        the public agent's model, tools and guard policy.
        """
        inner = _MemoryStore()
        internal, public = self._mounts(inner)
        request = AuthedRequestFactory().get("/internal/agent/runs/")
        store = self._step_store(internal, request)
        await store.register_run(_record("r1"))
        await store.save_snapshot(_snapshot("r1"))

        crossing = self._step_store(public, request)
        assert await crossing.latest_snapshot(run_id="r1") is None
