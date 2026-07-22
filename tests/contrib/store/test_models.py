from __future__ import annotations

from django_ag_ui.contrib.store.models import (
    StoredAttachment,
    StoredConversation,
    StoredRun,
    StoredSnapshot,
    StoredStepEvent,
    StoredToolEffect,
)


def test_str_prefers_title() -> None:
    assert str(StoredConversation(thread_id="t1", title="Trip planning")) == "Trip planning"


def test_str_falls_back_to_thread_id() -> None:
    assert str(StoredConversation(thread_id="t1", title="")) == "t1"


def test_attachment_str_prefers_name() -> None:
    assert str(StoredAttachment(attachment_id="a1", name="notes.txt")) == "notes.txt"


def test_attachment_str_falls_back_to_id() -> None:
    assert str(StoredAttachment(attachment_id="a1", name="")) == "a1"


def test_run_str_is_run_id() -> None:
    assert str(StoredRun(run_id="run-1")) == "run-1"


def test_step_event_str_is_run_and_kind() -> None:
    assert str(StoredStepEvent(run_id="run-1", kind="run_started")) == "run-1:run_started"


def test_snapshot_str_is_run_and_step() -> None:
    assert str(StoredSnapshot(run_id="run-1", step_index=3)) == "run-1@3"


def test_tool_effect_str_is_run_call_status() -> None:
    effect = StoredToolEffect(run_id="run-1", tool_call_id="c1", status="started")
    assert str(effect) == "run-1:c1:started"
