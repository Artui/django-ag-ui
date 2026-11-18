from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from django_spec_agents.headless.types.headless_start import HeadlessStart
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.thread_ref import ThreadRef

from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner


def start_from_sync(
    runner: AGUIHeadlessRunner,
    agent_id: str,
    prompt: str,
    principal: Any,
    *,
    timeout: float,
    thread_id: str | None = None,
    parent: ThreadRef | None = None,
    lineage: ChildLineageRecord | None = None,
) -> HeadlessStart:
    """Start a run from sync code: claimed and enqueued on a queued runner, else run to its end."""
    if runner.queued:
        return async_to_sync(runner.start)(
            agent_id,
            prompt,
            principal,
            timeout=timeout,
            thread_id=thread_id,
            parent=parent,
            lineage=lineage,
        )
    stop = async_to_sync(runner.run_to_first_stop)(
        agent_id,
        prompt,
        principal,
        timeout=timeout,
        thread_id=thread_id,
        parent=parent,
        lineage=lineage,
    )
    return HeadlessStart(thread_id=stop.thread_id, run_id=stop.run_id)


__all__ = ["start_from_sync"]
