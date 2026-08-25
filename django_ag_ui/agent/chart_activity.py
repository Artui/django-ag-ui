"""``chart_activity`` — an AG-UI event that draws a chart in the client."""

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
    """An event drawing ``spec``, or redrawing a chart already on screen.

    Emitted by the project from its own code, where it holds the data. There is
    no setting that turns this on: pushing a chart is an act rather than a mode,
    and a flag would suggest the framework emits one on your behalf, which it
    cannot -- it has no idea what you want charted.

    **The data never enters the model's context.** That is the whole reason to
    push rather than let the agent call a tool: a large or sensitive dataset is
    drawn for the user without being sent to the provider, and without costing a
    model round. The trade is that the model cannot then discuss what it never
    saw.

    ``chart_id`` is the identity of *this chart*, not of this event. Send the
    same one again to **replace** what is on screen -- a chart that redraws as a
    computation advances is one chart moving, and the client swaps it in place
    rather than stacking copies. Omit it and every call draws a new chart.
    """
    return ActivitySnapshotEvent(
        message_id=chart_id if chart_id is not None else f"chart-{uuid.uuid4()}",
        activity_type=CHART_ACTIVITY_TYPE,
        content=spec.as_content(),
        # Load-bearing only on a repeat id, and harmless otherwise: it is what
        # tells the client this supersedes the chart under that id rather than
        # being a second one that happens to share it.
        replace=True,
    )


__all__ = ["CHART_ACTIVITY_TYPE", "chart_activity"]
