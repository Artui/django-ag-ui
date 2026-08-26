"""The bounds a chart payload has to satisfy on *both* sides of the wire.

These mirror the web component's own limits exactly, and that is the whole
point of the module: the client refuses a payload outside them and has no
channel to say so, so a producer that does not know the same numbers ships
charts that vanish. Three constants and one validator, in one place, because the
first version of this feature bounded the consumer and forgot the producer --
and the failure looked like nothing happening at all.

Keep in step with ``MAX_MAGNITUDE``, ``MAX_POINTS`` and ``MAX_LABELS`` in the
component's ``src/ui/chart_spec_from.ts`` -- **all** of them, which is the
lesson the third one taught: mirroring two of the client's three bounds left the
same silent-drop hole the module exists to close. A mismatch is invisible in
both test suites: each side passes its own, and only a payload crossing the gap
between them fails.
"""

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
    """Refuse a number the client will not read, naming ``where`` it came from.

    ``Decimal`` is the case worth naming: a Django ``Sum`` over a
    ``DecimalField`` is the likeliest source of chart numbers in this ecosystem,
    and Pydantic serialises it as a JSON *string*. The client reads only JSON
    numbers and drops the whole chart, with no error on either side. Refused
    rather than coerced, because rounding somebody's money to a float behind
    their back is the wrong favour: call ``float()`` where the precision you are
    giving up is visible.

    ``bool`` is excluded for the reason it usually surprises people: it is an
    ``int`` to Python, so it would plot as 0 or 1 rather than announce itself.
    """
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
