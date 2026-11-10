from __future__ import annotations

import math

MAX_MAGNITUDE = 1e15
"""Largest absolute value a point may carry.

Finite is not sufficient. Two finite extremes still give an infinite *range*,
and the client divides by that range to scale -- yielding ``NaN`` in every
coordinate. It bounds magnitude to keep the range finite; so must this side.
"""

MAX_POINTS = 20_000
"""Most points a spec may carry, counting every series.

The client refuses more, because building the SVG for a spec that large blocks
the browser's main thread and, in a stored transcript, does so again on every
reload.
"""

MAX_LABELS = 2_000
"""Most labels a spec may carry, regardless of how many series read them.

A separate bound from ``MAX_POINTS`` because it answers a different question:
that one bounds the *data*, this one bounds the *DOM*. Every label emits an axis
text node whatever the series count, so a single-series spec well inside the
point budget can still ask the browser for tens of thousands of nodes -- and,
being in the transcript, ask again on every reload. One number cannot cover
both, which is why the client carries two and so does this side.
"""


def validate_point(where: str, point: object) -> None:
    """Refuse a number the client will not read, naming ``where`` it came from."""
    if isinstance(point, bool) or not isinstance(point, int | float):
        raise ValueError(
            f"{where} has a point of type {type(point).__name__}; the client reads only "
            f"JSON numbers, so convert it (float(...)) before charting"
        )
    if not math.isfinite(point):
        raise ValueError(
            f"{where} has a non-finite point ({point!r}); it serialises as null and the "
            f"client refuses the whole spec"
        )
    if abs(point) > MAX_MAGNITUDE:
        raise ValueError(
            f"{where} has a point of magnitude {abs(point):.3g}, over the client's "
            f"{MAX_MAGNITUDE:.0e} limit; it would be dropped on arrival with nothing reported"
        )


__all__ = ["MAX_LABELS", "MAX_MAGNITUDE", "MAX_POINTS", "validate_point"]
