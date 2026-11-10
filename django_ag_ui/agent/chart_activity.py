from __future__ import annotations

import uuid

from ag_ui.core import ActivitySnapshotEvent

from django_ag_ui.agent.types.chart_spec import ChartSpec

CHART_ACTIVITY_TYPE = "chart"
"""``activity_type`` the client matches on to draw a chart.

A **convention inside an extension point the protocol already provides**, not a
protocol extension: AG-UI defines the envelope and leaves ``activity_type`` an
open string. An ``ACTIVITY_SNAPSHOT`` rather than a ``CUSTOM`` event, for the
reason ``inject_compaction_events`` gives for the same choice -- the wire stays
vanilla AG-UI and ours is not a privileged client. A client that does not know
this name ignores the event, which is the graceful outcome.
"""


def chart_activity(spec: ChartSpec, *, chart_id: str | None = None) -> ActivitySnapshotEvent:
    """An event drawing ``spec``, or redrawing a chart already on screen."""
    return ActivitySnapshotEvent(
        message_id=chart_id if chart_id is not None else f"chart-{uuid.uuid4()}",
        activity_type=CHART_ACTIVITY_TYPE,
        content=spec.as_content(),
        replace=True,
    )


__all__ = ["CHART_ACTIVITY_TYPE", "chart_activity"]
