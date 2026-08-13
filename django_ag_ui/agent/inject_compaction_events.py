"""``inject_compaction_events`` — surface recorded compactions on the AG-UI stream."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from ag_ui.core import ActivitySnapshotEvent, BaseEvent

from django_ag_ui.agent.compaction_observer import COMPACTION_SINK, Compaction

#: ``activity_type`` clients match on to render the indicator.
COMPACTION_ACTIVITY_TYPE = "compaction"


async def inject_compaction_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, interleaving an activity event per recorded compaction.

    Establishes the per-run sink that ``CompactionObserver`` writes into,
    then drains it as the stream advances. The agent run happens *inside* this
    iteration, so a compaction surfaces immediately above the turn that ran with
    the shortened history.

    An ``ACTIVITY_SNAPSHOT`` rather than a ``CUSTOM`` event, so the wire stays
    vanilla AG-UI and ours is not a privileged client. Each carries a fresh
    ``message_id``: a compaction is a distinct occurrence, not a mutation of a
    prior one.

    The final drain after the loop is load-bearing — a run's last compaction is
    recorded during the model request that produces the closing events, and
    would otherwise be dropped.
    """
    sink: list[Compaction] = []
    token = COMPACTION_SINK.set(sink)
    try:
        async for event in stream:
            for compaction in _drain(sink):
                yield compaction
            yield event
        for compaction in _drain(sink):
            yield compaction
    finally:
        COMPACTION_SINK.reset(token)


def _drain(sink: list[Compaction]) -> list[BaseEvent]:
    """Take everything recorded so far, leaving the sink empty."""
    recorded, sink[:] = list(sink), []
    return [_to_event(compaction) for compaction in recorded]


def _to_event(compaction: Compaction) -> BaseEvent:
    return ActivitySnapshotEvent(
        message_id=f"compaction-{uuid.uuid4()}",
        activity_type=COMPACTION_ACTIVITY_TYPE,
        content={
            "removed": compaction.removed,
            "before": compaction.before,
            "after": compaction.after,
        },
    )


__all__ = ["COMPACTION_ACTIVITY_TYPE", "inject_compaction_events"]
