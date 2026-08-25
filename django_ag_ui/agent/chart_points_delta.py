"""``chart_points_delta`` — move one series' numbers without re-sending the chart."""

from __future__ import annotations

from typing import Any

from ag_ui.core import ActivityDeltaEvent

from django_ag_ui.agent.chart_activity import CHART_ACTIVITY_TYPE


def chart_points_delta(
    chart_id: str,
    *,
    series: int = 0,
    points: tuple[float, ...] | list[float],
) -> ActivityDeltaEvent:
    """Replace one series' points on the chart already under ``chart_id``.

    The cheap half of live updating. A snapshot re-sends the whole spec, which
    is right when the shape changes; this sends a JSON Patch touching one array,
    which is right when a long-running computation is only moving the numbers.

    ``chart_id`` must name a chart the client has already drawn. A delta for an
    id it does not hold is dropped -- there is nothing to patch -- so send the
    snapshot first and keep the id.

    ``series`` is the index in the spec's series list, and the caller is
    responsible for it matching what was sent: a patch is applied positionally
    and cannot tell that series 2 is now something else. Send a fresh snapshot
    when the *shape* changes and reserve this for when it has not.
    """
    patch: list[dict[str, Any]] = [
        {"op": "replace", "path": f"/series/{series}/points", "value": list(points)}
    ]
    return ActivityDeltaEvent(
        message_id=chart_id,
        activity_type=CHART_ACTIVITY_TYPE,
        patch=patch,
    )


__all__ = ["chart_points_delta"]
