from __future__ import annotations

from django_spec_agents.persistence.types.thread_run_state import ThreadRunState
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus

from django_ag_ui.persistence.thread_status_fields import thread_status_fields


def test_a_waiting_state_with_no_recorded_call_serves_an_empty_interrupt_list() -> None:
    state = ThreadRunState(thread_id="t1", status=ThreadRunStatus.WAITING)

    assert thread_status_fields(state) == {
        "status": "waiting",
        "last_read_at": None,
        "interrupts": [],
    }


def test_a_failed_state_with_no_reason_serves_a_null_reason() -> None:
    state = ThreadRunState(thread_id="t1", status=ThreadRunStatus.FAILED)

    assert thread_status_fields(state)["reason"] is None


def test_no_state_reads_idle() -> None:
    assert thread_status_fields(None) == {"status": "idle", "last_read_at": None}
