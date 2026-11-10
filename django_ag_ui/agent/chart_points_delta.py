from __future__ import annotations

from typing import Any

from ag_ui.core import ActivityDeltaEvent

from django_ag_ui.agent.chart_activity import CHART_ACTIVITY_TYPE
from django_ag_ui.agent.chart_limits import validate_point
from django_ag_ui.agent.types.chart_spec import ChartSpec


def chart_points_delta(
    chart_id: str,
    *,
    series: int = 0,
    points: tuple[float, ...] | list[float],
    spec: ChartSpec | None = None,
) -> ActivityDeltaEvent:
    """Replace one series' points on the chart already under ``chart_id``."""
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
    if spec is not None:
        if series >= len(spec.series):
            raise ValueError(
                f"delta for {chart_id!r} names series {series} of a chart that has "
                f"{len(spec.series)}; the patch path would not resolve, so the client "
                f"would keep the numbers it already has and report nothing"
            )
        if len(points) != len(spec.labels):
            raise ValueError(
                f"delta for {chart_id!r} carries {len(points)} points for a chart of "
                f"{len(spec.labels)} labels (series {spec.series[series].label!r}); the "
                f"patch applies cleanly and then leaves a spec the client refuses to "
                f"redraw -- stale numbers on screen, and the chart gone on the next "
                f"reload. Send a fresh snapshot when the shape changes."
            )
    patch: list[dict[str, Any]] = [
        {"op": "replace", "path": f"/series/{series}/points", "value": list(points)}
    ]
    return ActivityDeltaEvent(
        message_id=chart_id,
        activity_type=CHART_ACTIVITY_TYPE,
        patch=patch,
    )


__all__ = ["chart_points_delta"]
