"""``inject_invalidation_events`` -- surface queued invalidations on the stream."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ag_ui.core import BaseEvent, CustomEvent

from django_ag_ui.agent.publish_invalidation import INVALIDATION_SINK


async def inject_invalidation_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, interleaving each invalidation queued so far.

    Establishes the per-run sink :func:`publish_invalidation` writes into, then
    drains it as the stream advances -- so an invalidation announced by a
    post-commit callback reaches the page **during** the run, next to the write
    that caused it, rather than at the end.

    That immediacy is the point of the queue rather than a nicety: a long
    multi-step run should refresh the list as its third write lands, not five
    minutes later when everything finishes.

    The final drain after the loop is load-bearing, for the reason
    ``inject_compaction_events`` gives: a write committed during the model
    request that produces the closing events would otherwise be dropped.
    """
    sink: list[CustomEvent] = []
    token = INVALIDATION_SINK.set(sink)
    try:
        async for event in stream:
            for queued in _drain(sink):
                yield queued
            yield event
        for queued in _drain(sink):
            yield queued
    finally:
        INVALIDATION_SINK.reset(token)


def _drain(sink: list[CustomEvent]) -> list[CustomEvent]:
    """Take everything queued so far, leaving the sink empty."""
    queued, sink[:] = list(sink), []
    return queued


__all__ = ["inject_invalidation_events"]
