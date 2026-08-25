"""``ChartKind`` — how the client draws a spec."""

from __future__ import annotations

from typing import Literal

ChartKind = Literal["bar", "line", "pie", "scatter", "stacked"]
"""How the client draws a spec.

An unrecognised value is drawn as a bar rather than refused: the numbers are
still worth showing, and a chart that appears in the wrong shape is easier to
notice than one that never appears at all.
"""


__all__ = ["ChartKind"]
