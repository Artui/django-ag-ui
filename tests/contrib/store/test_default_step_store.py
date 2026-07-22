from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    StepStore,
    ToolEffectRecord,
)

from django_ag_ui.contrib.store.default_step_store import DefaultStepStore
from django_ag_ui.contrib.store.models import StoredRun, StoredSnapshot, StoredToolEffect

# transaction=True: the store writes through ``sync_to_async``, so its ORM calls
# run on a different connection than a transaction-wrapped test would roll back —
# table truncation between tests is what actually isolates them (the same reason
# the async view tests use it).
pytestmark = pytest.mark.django_db(transaction=True)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _authed(pk: str = "7") -> HttpRequest:
    """A request whose user resolves to ``owner_id == pk`` (no DB / session)."""
    request = RequestFactory().post("/")
    request.user = SimpleNamespace(is_authenticated=True, pk=pk)  # type: ignore[attr-defined]
    return request


def _anon() -> HttpRequest:
    request = RequestFactory().post("/")
    request.user = SimpleNamespace(is_authenticated=False, pk=None)  # type: ignore[attr-defined]
    return request


def _store(pk: str = "7") -> DefaultStepStore:
    return DefaultStepStore(_authed(pk))


def _messages() -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content="find the bug")]),
        ModelResponse(parts=[TextPart(content="found it")]),
    ]


# -- Protocol conformance -----------------------------------------------------


def test_satisfies_the_step_store_protocol() -> None:
    assert isinstance(_store(), StepStore)


# -- Runs ---------------------------------------------------------------------


async def test_register_and_get_run_round_trips() -> None:
    record = RunRecord(
        run_id="r1",
        conversation_id="c1",
        parent_run_id="p1",
        agent_name="librarian",
        metadata={"k": "v"},
        started_at=_T0,
    )
    store = _store()
    await store.register_run(record)
    assert await store.get_run(run_id="r1") == record


async def test_get_run_missing_returns_none() -> None:
    assert await _store().get_run(run_id="absent") is None


async def test_register_run_overwrites_on_same_owner_and_run() -> None:
    store = _store()
    await store.register_run(RunRecord(run_id="r1", agent_name="first", started_at=_T0))
    await store.register_run(RunRecord(run_id="r1", agent_name="second", started_at=_T0))
    got = await store.get_run(run_id="r1")
    assert got is not None and got.agent_name == "second"
    assert await StoredRun.objects.filter(owner_id="7", run_id="r1").acount() == 1


async def test_list_runs_filters_and_orders_by_started_at() -> None:
    store = _store()
    await store.register_run(
        RunRecord(run_id="r1", conversation_id="c1", parent_run_id="p1", started_at=_T0)
    )
    await store.register_run(
        RunRecord(
            run_id="r2",
            conversation_id="c1",
            parent_run_id="p2",
            started_at=_T0 + timedelta(minutes=1),
        )
    )
    await store.register_run(
        RunRecord(
            run_id="r3",
            conversation_id="c2",
            parent_run_id="p1",
            started_at=_T0 + timedelta(minutes=2),
        )
    )

    assert [r.run_id for r in await store.list_runs()] == ["r1", "r2", "r3"]
    assert [r.run_id for r in await store.list_runs(parent_run_id="p1")] == ["r1", "r3"]
    assert [r.run_id for r in await store.list_runs(conversation_id="c1")] == ["r1", "r2"]
    assert [r.run_id for r in await store.list_runs(parent_run_id="p1", conversation_id="c1")] == [
        "r1"
    ]


# -- Events -------------------------------------------------------------------


async def test_append_and_list_events_in_insertion_order() -> None:
    store = _store()
    await store.append_event(StepEvent(run_id="r1", kind="run_started", step_index=0))
    await store.append_event(
        StepEvent(
            run_id="r1",
            kind="tool_call_started",
            step_index=1,
            tool_call_id="call-1",
            tool_name="search",
            metadata={"trace": "abc"},
        )
    )
    await store.append_event(
        StepEvent(run_id="r1", kind="tool_call_failed", step_index=2, error="boom")
    )

    events = await store.list_events(run_id="r1")
    assert [e.kind for e in events] == ["run_started", "tool_call_started", "tool_call_failed"]
    assert events[1].tool_call_id == "call-1"
    assert events[1].tool_name == "search"
    assert events[1].metadata == {"trace": "abc"}
    assert events[2].error == "boom"


async def test_list_events_empty_run_is_empty() -> None:
    assert await _store().list_events(run_id="absent") == []


# -- Snapshots ----------------------------------------------------------------


async def test_save_and_latest_snapshot_round_trips_messages() -> None:
    messages = _messages()
    store = _store()
    await store.save_snapshot(
        ContinuableSnapshot(run_id="r1", step_index=4, messages=messages, conversation_id="c1")
    )
    got = await store.latest_snapshot(run_id="r1")
    assert got is not None
    assert got.step_index == 4
    assert got.conversation_id == "c1"
    assert ModelMessagesTypeAdapter.dump_python(
        got.messages, mode="json"
    ) == ModelMessagesTypeAdapter.dump_python(messages, mode="json")


async def test_latest_snapshot_is_by_insertion_not_step_index() -> None:
    store = _store()
    await store.save_snapshot(ContinuableSnapshot(run_id="r1", step_index=5, messages=_messages()))
    await store.save_snapshot(ContinuableSnapshot(run_id="r1", step_index=2, messages=_messages()))
    got = await store.latest_snapshot(run_id="r1")
    # The most recently written snapshot wins even though its step_index is lower.
    assert got is not None and got.step_index == 2


async def test_latest_snapshot_missing_returns_none() -> None:
    assert await _store().latest_snapshot(run_id="absent") is None


# -- Tool effects -------------------------------------------------------------


async def test_record_tool_effect_upserts_on_run_and_call() -> None:
    store = _store()
    await store.record_tool_effect(
        ToolEffectRecord(tool_call_id="c1", tool_name="ship", run_id="r1", status="started")
    )
    await store.record_tool_effect(
        ToolEffectRecord(
            tool_call_id="c1",
            tool_name="ship",
            run_id="r1",
            status="completed",
            ended_at=_T0,
            idempotency_key="idem-1",
            effect_summary="shipped order 42",
        )
    )
    got = await store.get_tool_effect(run_id="r1", tool_call_id="c1")
    assert got is not None
    assert got.status == "completed"
    assert got.idempotency_key == "idem-1"
    assert got.effect_summary == "shipped order 42"
    assert await StoredToolEffect.objects.filter(owner_id="7", run_id="r1").acount() == 1


async def test_get_tool_effect_missing_returns_none() -> None:
    assert await _store().get_tool_effect(run_id="r1", tool_call_id="absent") is None


async def test_list_unresolved_returns_only_started_effects() -> None:
    store = _store()
    await store.record_tool_effect(
        ToolEffectRecord(tool_call_id="c1", tool_name="a", run_id="r1", status="started")
    )
    await store.record_tool_effect(
        ToolEffectRecord(tool_call_id="c2", tool_name="b", run_id="r1", status="completed")
    )
    unresolved = await store.list_unresolved_tool_effects(run_id="r1")
    assert [e.tool_call_id for e in unresolved] == ["c1"]


# -- Owner scoping (the security boundary) ------------------------------------


async def test_runs_snapshots_and_effects_are_owner_scoped() -> None:
    a, b = _store("7"), _store("99")
    await a.register_run(RunRecord(run_id="r1", agent_name="mine", started_at=_T0))
    await a.save_snapshot(ContinuableSnapshot(run_id="r1", step_index=0, messages=_messages()))
    await a.record_tool_effect(
        ToolEffectRecord(tool_call_id="c1", tool_name="x", run_id="r1", status="started")
    )

    # A different owner cannot read A's run by guessing the run_id.
    assert await b.get_run(run_id="r1") is None
    assert await b.latest_snapshot(run_id="r1") is None
    assert await b.get_tool_effect(run_id="r1", tool_call_id="c1") is None
    assert await b.list_runs() == []
    assert await b.list_events(run_id="r1") == []
    assert await b.list_unresolved_tool_effects(run_id="r1") == []

    # ...while A still sees its own.
    assert (await a.get_run(run_id="r1")) is not None


# -- Anonymous handling -------------------------------------------------------


async def test_anonymous_disallowed_no_ops_every_method() -> None:
    store = DefaultStepStore(_anon())  # ALLOW_ANONYMOUS defaults off
    await store.register_run(RunRecord(run_id="r1", started_at=_T0))
    await store.append_event(StepEvent(run_id="r1", kind="run_started", step_index=0))
    await store.save_snapshot(ContinuableSnapshot(run_id="r1", step_index=0, messages=[]))
    await store.record_tool_effect(
        ToolEffectRecord(tool_call_id="c1", tool_name="x", run_id="r1", status="started")
    )

    assert await store.get_run(run_id="r1") is None
    assert await store.list_runs() == []
    assert await store.list_events(run_id="r1") == []
    assert await store.latest_snapshot(run_id="r1") is None
    assert await store.get_tool_effect(run_id="r1", tool_call_id="c1") is None
    assert await store.list_unresolved_tool_effects(run_id="r1") == []
    # Nothing was written for the anonymous request.
    assert await StoredRun.objects.acount() == 0
    assert await StoredSnapshot.objects.acount() == 0
    assert await StoredToolEffect.objects.acount() == 0


@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.cache")
async def test_anonymous_allowed_persists_under_a_session_bucket() -> None:
    from django.contrib.sessions.backends.cache import SessionStore

    request = _anon()
    request.session = SessionStore()  # type: ignore[attr-defined]
    store = DefaultStepStore(request, allow_anonymous=True)
    await store.register_run(RunRecord(run_id="r1", agent_name="anon-run", started_at=_T0))

    got = await store.get_run(run_id="r1")
    assert got is not None and got.agent_name == "anon-run"
    row = await StoredRun.objects.aget(run_id="r1")
    assert row.owner_id.startswith("anon:")
