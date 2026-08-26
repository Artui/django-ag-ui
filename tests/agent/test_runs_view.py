from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord

from django_ag_ui.agent.runs_view import RunsView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory

_STARTED = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _record(
    run_id: str, *, parent: str | None = None, thread: str | None = "t1", minutes: int = 0
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id=thread,
        parent_run_id=parent,
        agent_name=None,
        metadata={},
        started_at=_STARTED + timedelta(minutes=minutes),
    )


def _snapshot(run_id: str, *, said: Any = None) -> ContinuableSnapshot:
    """A snapshot, optionally holding the user prompt the row previews.

    ``said`` is the ``UserPromptPart`` content verbatim, so a test can hand over
    the multi-modal sequence form as readily as a string.
    """
    messages: list[Any] = []
    if said is not None:
        # An assistant turn first, so finding the prompt is a search rather than
        # reading ``messages[0]``.
        messages = [
            ModelResponse(parts=[TextPart(content="working on it")]),
            ModelRequest(parts=[UserPromptPart(content=said)]),
        ]
    return ContinuableSnapshot(
        run_id=run_id,
        step_index=0,
        messages=messages,
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


def _get(path: str = "/runs/", *, anonymous: bool = False) -> HttpRequest:
    # Authenticated by default: the index is owner-scoped and the view refuses
    # anonymous callers, so a fixture that wants rows has to be a logged-in one.
    factory = RequestFactory() if anonymous else AuthedRequestFactory()
    return factory.get(path)


async def _body(response: Any) -> dict[str, Any]:
    return json.loads(response.content)


class TestListing:
    async def test_lists_runs(self) -> None:
        store = _FakeStore([_record("r1"), _record("r2")])
        response = await RunsView(_factory(store))(_get())

        assert response.status_code == 200
        assert {row["run_id"] for row in (await _body(response))["runs"]} == {"r1", "r2"}

    async def test_serves_the_newest_run_first(self) -> None:
        """The store answers oldest-first; a person reads a picker top-down.

        Ascending ``started_at`` is the harness protocol's documented order, so
        the store is right and this view owns the reading order.
        """
        store = _FakeStore(
            [_record("oldest"), _record("middle", minutes=1), _record("newest", minutes=2)]
        )
        rows = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert [row["run_id"] for row in rows] == ["newest", "middle", "oldest"]

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
        store = _FakeStore([_record("r1")], {"r1": _snapshot("r1", said="Move standup to Friday")})
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row == {
            "run_id": "r1",
            "thread_id": "t1",
            "parent_run_id": None,
            "started_at": "2026-07-27T12:00:00+00:00",
            "continuable": True,
            "preview": "Move standup to Friday",
        }


class TestPreview:
    """The row's only human-readable field, from the snapshot already loaded."""

    async def test_previews_the_first_user_message(self) -> None:
        store = _FakeStore(
            [_record("r1"), _record("r2", minutes=1)],
            {
                "r1": _snapshot("r1", said="What is on the board?"),
                "r2": _snapshot("r2", said="Import these three events"),
            },
        )
        rows = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert {row["run_id"]: row["preview"] for row in rows} == {
            "r1": "What is on the board?",
            "r2": "Import these three events",
        }

    async def test_costs_no_extra_query(self) -> None:
        """One snapshot read per row, the one ``continuable`` already needed."""
        store = _FakeStore([_record("r1")], {"r1": _snapshot("r1", said="hello")})
        await RunsView(_factory(store))(_get())

        assert store.snapshot_calls == ["r1"]

    async def test_a_run_with_no_snapshot_previews_nothing(self) -> None:
        """Null exactly where ``continuable`` is false, so no row promises words it lacks."""
        store = _FakeStore([_record("r1")])
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert (row["continuable"], row["preview"]) == (False, None)

    async def test_a_run_seeded_from_history_alone_previews_nothing(self) -> None:
        store = _FakeStore([_record("r1")], {"r1": _snapshot("r1")})
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row["preview"] is None

    async def test_a_multi_modal_prompt_previews_the_words_in_it(self) -> None:
        """A prompt is a string or a sequence; only the strings were typed."""
        store = _FakeStore(
            [_record("r1")],
            {
                "r1": _snapshot(
                    "r1", said=["Read this", BinaryContent(data=b"x", media_type="image/png")]
                )
            },
        )
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row["preview"] == "Read this"

    async def test_a_prompt_with_no_words_previews_nothing(self) -> None:
        store = _FakeStore(
            [_record("r1")],
            {"r1": _snapshot("r1", said=[BinaryContent(data=b"x", media_type="image/png")])},
        )
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row["preview"] is None

    async def test_a_blank_prompt_previews_nothing(self) -> None:
        store = _FakeStore([_record("r1")], {"r1": _snapshot("r1", said="   \n  ")})
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row["preview"] is None

    async def test_a_pasted_block_arrives_as_one_line(self) -> None:
        """The row is one line; a paragraph would be the client's problem to clean."""
        store = _FakeStore(
            [_record("r1")], {"r1": _snapshot("r1", said="Import:\n\nMon, 9:00\nTue,  10:00")}
        )
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert row["preview"] == "Import: Mon, 9:00 Tue, 10:00"

    async def test_a_long_prompt_is_truncated(self) -> None:
        store = _FakeStore([_record("r1")], {"r1": _snapshot("r1", said="ab " * 60)})
        (row,) = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        preview = row["preview"]
        assert preview.endswith("…")
        assert len(preview) <= 101

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
        response = await RunsView(_factory(_FakeStore()))(AuthedRequestFactory().post("/runs/"))
        assert response.status_code == 405

    async def test_anonymous_is_refused_by_default(self) -> None:
        response = await RunsView(_factory(_FakeStore()))(_get(anonymous=True))

        assert response.status_code == 401

    async def test_anonymous_is_served_when_authentication_is_waived(self) -> None:
        view = RunsView(_factory(_FakeStore()), require_authenticated=False)
        response = await view(_get(anonymous=True))

        assert response.status_code == 200

    async def test_an_authorize_predicate_can_deny(self) -> None:
        response = await RunsView(_factory(_FakeStore()), authorize=lambda request: False)(_get())
        assert response.status_code == 403

    async def test_an_anonymous_store_refusal_is_403_not_500(self) -> None:
        # Only reachable with authentication deliberately waived: otherwise the
        # anonymous request never reaches the store that refuses it.
        store = _FakeStore(raises=AnonymousOperationError("anonymous"))
        view = RunsView(_factory(store), require_authenticated=False)
        response = await view(_get(anonymous=True))

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


class TestBounding:
    """``RUN_LIST_LIMIT`` bounds the expensive half of the response.

    Every row costs a ``latest_snapshot`` call and holds that run's whole message
    list resident while the rows are built, so an account with a long history
    turned one GET into ``1 + N`` queries and ``N`` transcripts in memory. The
    store's own ``list_runs`` still answers unbounded -- the harness protocol has
    no limit to pass -- so the cap is applied here, before any snapshot is read.
    """

    @override_settings(DJANGO_AG_UI={"RUN_LIST_LIMIT": 2})
    async def test_the_response_is_capped(self) -> None:
        store = _FakeStore([_record(f"r{i}", minutes=i) for i in range(10)])
        rows = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert len(rows) == 2

    @override_settings(DJANGO_AG_UI={"RUN_LIST_LIMIT": 2})
    async def test_the_cap_keeps_the_newest_runs(self) -> None:
        """A picker showing the *oldest* two would be worse than showing none."""
        store = _FakeStore([_record(f"r{i}", minutes=i) for i in range(5)])
        rows = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert [row["run_id"] for row in rows] == ["r4", "r3"]

    @override_settings(DJANGO_AG_UI={"RUN_LIST_LIMIT": 2})
    async def test_the_dropped_runs_cost_no_snapshot_load(self) -> None:
        """The point of the cap: it bounds the queries, not just the JSON.

        Trimming after the loop would still pay for every run in the ledger --
        which is the whole cost this finds.
        """
        store = _FakeStore([_record(f"r{i}", minutes=i) for i in range(10)])
        await RunsView(_factory(store))(_get())

        assert store.snapshot_calls == ["r8", "r9"]

    @override_settings(DJANGO_AG_UI={"RUN_LIST_LIMIT": 0})
    async def test_zero_disables_the_cap(self) -> None:
        store = _FakeStore([_record(f"r{i}", minutes=i) for i in range(10)])
        rows = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert len(rows) == 10

    async def test_a_shorter_ledger_than_the_cap_is_served_whole(self) -> None:
        store = _FakeStore([_record("r1"), _record("r2", minutes=1)])
        rows = (await _body(await RunsView(_factory(store))(_get())))["runs"]

        assert len(rows) == 2

    async def test_the_ceiling_is_per_endpoint(self) -> None:
        """Two mounts, two ceilings — the reason it rides the config record."""
        store = _FakeStore([_record(f"r{i}", minutes=i) for i in range(6)])
        strict = RunsView(_factory(store), config=build_ag_ui_config(run_list_limit=1))
        loose = RunsView(_factory(store), config=build_ag_ui_config(run_list_limit=4))

        assert len((await _body(await strict(_get())))["runs"]) == 1
        assert len((await _body(await loose(_get())))["runs"]) == 4
