"""``chart_points_delta`` — move one series' numbers without re-sending the chart."""

from __future__ import annotations

from typing import Any

from ag_ui.core import ActivityDeltaEvent

from django_ag_ui.agent.chart_activity import CHART_ACTIVITY_TYPE
from django_ag_ui.agent.chart_limits import validate_point


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

    ``series`` is the index in the spec's series list, and ``points`` must be
    the **same length** as the series it replaces. Neither can be checked here,
    and both fail the same quiet way: a patch is applied positionally, so it
    cannot tell that series 2 is now something else, and a wrong-length array
    applies cleanly and leaves a chart the client then refuses to redraw --
    stale numbers on screen, and the chart gone entirely on the next reload.

    So: send a fresh snapshot whenever the *shape* changes, and reserve this for
    when only the values move. An index past the end is refused by the client
    rather than applied, which at least fails visibly in a console; a
    wrong-length array is the one that fails invisibly.
    """
    # Everything below is refused here because the client cannot report any of
    # it. A patch it cannot apply is caught, warned about in a console nobody is
    # watching, and the chart simply does not move -- leaving the *previous*
    # numbers on screen, which reads as a chart that is up to date rather than
    # one that failed. Silence is the failure mode this whole helper has to
    # design against.
    if series < 0:
        raise ValueError(
            f"series index must not be negative; got {series}. The patch path would "
            f"never resolve and the chart would keep its old numbers."
        )
    if not points:
        raise ValueError(
            "a delta needs at least one point; an empty array patches the chart into a "
            "shape ChartSpec itself refuses, and the client then drops it"
        )
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
