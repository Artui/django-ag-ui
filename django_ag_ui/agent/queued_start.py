from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence
from typing import Any

from asgiref.sync import async_to_sync
from django_spec_agents.headless.types.firing_queue import FiringQueue
from django_spec_agents.headless.types.headless_firing import HeadlessFiring
from django_spec_agents.headless.types.headless_runner import HeadlessRunner
from django_spec_agents.headless.types.headless_start import HeadlessStart
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from pydantic_ai.capabilities import AbstractCapability

RUN_HEADLESS_SPEC = "run_headless"


def enqueue_headless(
    queue: FiringQueue,
    agent_id: str,
    prompt: str,
    principal: Any,
    *,
    timeout: float,
    thread_id: str | None = None,
    parent: ThreadRef | None = None,
    lineage: ChildLineageRecord | None = None,
    capabilities: Sequence[AbstractCapability[Any]] = (),
    spec_name: str = RUN_HEADLESS_SPEC,
) -> HeadlessStart:
    """Enqueue ``run_headless`` for the start; its ``run_id`` is the queue's id for the firing."""
    if capabilities:
        raise ValueError(
            "a queued headless start carries no capabilities=: they cannot cross a queue"
        )
    firing = HeadlessFiring(
        agent_id=agent_id,
        prompt=prompt,
        timeout=timeout,
        thread_id=thread_id or uuid.uuid4().hex,
        parent=parent,
        lineage=lineage,
    )
    assert firing.thread_id is not None
    firing_id = queue.enqueue(spec_name, dataclasses.asdict(firing), actor=principal)
    return HeadlessStart(thread_id=firing.thread_id, run_id=firing_id)


def start_from_sync(
    runner: HeadlessRunner,
    queue: FiringQueue | None,
    agent_id: str,
    prompt: str,
    principal: Any,
    *,
    timeout: float,
    thread_id: str | None = None,
    parent: ThreadRef | None = None,
    lineage: ChildLineageRecord | None = None,
    spec_name: str = RUN_HEADLESS_SPEC,
) -> HeadlessStart:
    """Start a run from sync code: enqueued with a queue, else run to its first stop here."""
    if queue is not None:
        return enqueue_headless(
            queue,
            agent_id,
            prompt,
            principal,
            timeout=timeout,
            thread_id=thread_id,
            parent=parent,
            lineage=lineage,
            spec_name=spec_name,
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


__all__ = ["RUN_HEADLESS_SPEC", "enqueue_headless", "start_from_sync"]
