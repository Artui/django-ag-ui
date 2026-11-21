from __future__ import annotations

from typing import Any

from ag_ui.core import Interrupt
from django_spec_agents.persistence.types.run_waiting import RunWaiting
from django_spec_agents.persistence.types.thread_run_state import ThreadRunState
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus

# pydantic-ai's prefix and reason (``approval_to_interrupt``), so a resume answers either.
_INTERRUPT_ID_PREFIX = "int-"
_INTERRUPT_REASON = "tool_call"


def thread_status_fields(state: ThreadRunState | None) -> dict[str, Any]:
    """The status keys a thread row gains from its run state; no state reads ``idle``."""
    if state is None:
        return {"status": str(ThreadRunStatus.IDLE), "last_read_at": None}
    fields: dict[str, Any] = {
        "status": str(state.status),
        "last_read_at": None if state.last_read_at is None else state.last_read_at.isoformat(),
    }
    if state.status is ThreadRunStatus.FAILED:
        fields["reason"] = None if state.reason is None else str(state.reason)
    if state.status is ThreadRunStatus.WAITING:
        fields["interrupts"] = [] if state.waiting is None else [_interrupt(state.waiting)]
    return fields


def _interrupt(waiting: RunWaiting) -> dict[str, Any]:
    interrupt = Interrupt(
        id=f"{_INTERRUPT_ID_PREFIX}{waiting.tool_call_id}",
        reason=_INTERRUPT_REASON,
        message=waiting.summary,
        tool_call_id=waiting.tool_call_id,
        metadata={"destructive": waiting.destructive, "kind": str(waiting.kind)},
    )
    return interrupt.model_dump(mode="json", by_alias=True, exclude_none=True)


__all__ = ["thread_status_fields"]
