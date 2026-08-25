"""``ChartSeries`` — one named run of numbers in a chart."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChartSeries:
    """One named series, carrying exactly one point per chart label.

    The length agreement is checked by
    [`ChartSpec`][django_ag_ui.ChartSpec], which is the only thing that knows
    how many labels there are.
    """

    label: str
    points: tuple[float, ...]


__all__ = ["ChartSeries"]
