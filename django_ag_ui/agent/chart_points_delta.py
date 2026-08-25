"""``chart_points_delta`` — move one series' numbers without re-sending the chart."""

from __future__ import annotations

from typing import Any

from ag_ui.core import ActivityDeltaEvent

from django_ag_ui.agent.chart_activity import CHART_ACTIVITY_TYPE
from django_ag_ui.agent.types.chart_spec import validate_point


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
    if series < 0:
        # The client resolves this path positionally and a negative index never
        # resolves: it warns and the chart does not move, so the mistake is
        # invisible on both sides unless it is caught here.
        raise ValueError(f"series index must not be negative; got {series}")
    for point in points:
        validate_point(f"delta for {chart_id!r}", point)
    patch: list[dict[str, Any]] = [
        {"op": "replace", "path": f"/series/{series}/points", "value": list(points)}
    ]
    return ActivityDeltaEvent(
        message_id=chart_id,
        activity_type=CHART_ACTIVITY_TYPE,
        patch=patch,
    )


__all__ = ["chart_points_delta"]
