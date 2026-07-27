from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord

from django_ag_ui.agent.runs_view import RunsView

_STARTED = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _record(run_id: str, *, parent: str | None = None, thread: str | None = "t1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id=thread,
        parent_run_id=parent,
        agent_name=None,
        metadata={},
        started_at=_STARTED,
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


class _FakeStore:
    """An in-memory step store exercising the view without a DB."""

    def __init__(
        self,
        runs: list[RunRecord] | None = None,
        snapshots: dict[str, ContinuableSnapshot] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.runs = runs or []
        self.snapshots = snapshots or {}
        self.raises = raises
        self.snapshot_calls: list[str] = []

    async def list_runs(self, **kwargs: Any) -> list[RunRecord]:
        if self.raises is not None:
            raise self.raises
        return self.runs

    async def latest_snapshot(self, *, run_id: str) -> ContinuableSnapshot | None:
        self.snapshot_calls.append(run_id)
        return self.snapshots.get(run_id)


def _factory(store: _FakeStore) -> Any:
    """The ``request -> StepStore`` shape the view is configured with."""
    return lambda request: store


def _get(path: str = "/runs/") -> HttpRequest:
    return RequestFactory().get(path)


async def _body(response: Any) -> dict[str, Any]:
    return json.loads(response.content)


class TestListing:
    async def test_lists_runs(self) -> None:
        store = _FakeStore([_record("r1"), _record("r2")])
        response = await RunsView(_factory(store))(_get())

        assert response.status_code == 200
        assert [row["run_id"] for row in (await _body(response))["runs"]] == ["r1", "r2"]

    async def test_reports_continuable_per_run(self) -> None:
        store = _FakeStore([_record("r1"), _record("r2")], {"r1": _snapshot("r1")})
        rows = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert {row["run_id"]: row["continuable"] for row in rows} == {"r1": True, "r2": False}

    async def test_continuable_uses_the_same_call_resume_makes(self) -> None:
        """Not approximated from event counts — it asks for the snapshot."""
        store = _FakeStore([_record("r1"), _record("r2")])
        await RunsView(_factory(store))(_get())

        assert store.snapshot_calls == ["r1", "r2"]

    async def test_exposes_fork_lineage(self) -> None:
        store = _FakeStore([_record("r2", parent="r1")])
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row["parent_run_id"] == "r1"

    async def test_row_shape(self) -> None:
        store = _FakeStore([_record("r1")], {"r1": _snapshot("r1")})
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row == {
            "run_id": "r1",
            "thread_id": "t1",
            "parent_run_id": None,
            "started_at": "2026-07-27T12:00:00+00:00",
            "continuable": True,
        }

    async def test_owner_scoping_stays_server_side(self) -> None:
        """No owner field on the wire — the store filters, the client isn't told."""
        store = _FakeStore([_record("r1")])
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert "owner_id" not in row
        assert "owner" not in row

    async def test_no_runs_is_an_empty_list(self) -> None:
        assert (await _body(await RunsView(_factory(_FakeStore()))(_get())))["runs"] == []

    async def test_a_run_with_no_thread_reports_null(self) -> None:
        store = _FakeStore([_record("r1", thread=None)])
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row["thread_id"] is None

    async def test_the_store_is_built_per_request(self) -> None:
        """The harness protocol carries no request, so the factory binds one."""
        seen: list[HttpRequest] = []
        store = _FakeStore([_record("r1")])

        def factory(request: HttpRequest) -> _FakeStore:
            seen.append(request)
            return store

        request = _get()
        await RunsView(factory)(request)

        assert seen == [request]


class TestMethodAndAuth:
    async def test_post_is_not_allowed(self) -> None:
        response = await RunsView(_factory(_FakeStore()))(RequestFactory().post("/runs/"))
        assert response.status_code == 405

    async def test_anonymous_is_refused_when_authentication_is_required(self) -> None:
        request = _get()
        request.user = None  # type: ignore[attr-defined]
        response = await RunsView(_factory(_FakeStore()), require_authenticated=True)(request)

        assert response.status_code == 401

    async def test_an_authorize_predicate_can_deny(self) -> None:
        response = await RunsView(_factory(_FakeStore()), authorize=lambda request: False)(_get())
        assert response.status_code == 403

    async def test_an_anonymous_store_refusal_is_403_not_500(self) -> None:
        store = _FakeStore(raises=AnonymousOperationError("anonymous"))
        response = await RunsView(_factory(store))(_get())

        assert response.status_code == 403

    async def test_auth_runs_before_the_store_is_touched(self) -> None:
        """A denied request must not build a store or hit the DB."""
        built: list[HttpRequest] = []

        def factory(request: HttpRequest) -> _FakeStore:
            built.append(request)
            return _FakeStore()

        response = await RunsView(factory, authorize=lambda request: False)(_get())

        assert response.status_code == 403
        assert built == []
