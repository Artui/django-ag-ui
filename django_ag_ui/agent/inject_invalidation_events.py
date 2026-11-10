from __future__ import annotations

from collections.abc import AsyncIterator

from ag_ui.core import BaseEvent, CustomEvent

from django_ag_ui.agent.publish_invalidation import INVALIDATION_SINK


async def inject_invalidation_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, interleaving each invalidation queued so far."""
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
