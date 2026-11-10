from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChartSeries:
    """One named series, carrying exactly one point per chart label."""

    label: str
    points: tuple[float, ...]


__all__ = ["ChartSeries"]
