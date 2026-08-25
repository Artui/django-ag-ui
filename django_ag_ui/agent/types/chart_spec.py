"""``ChartSpec`` — the data a chart is drawn from, on the way to the browser."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from django_ag_ui.agent.types.chart_kind import ChartKind
from django_ag_ui.agent.types.chart_series import ChartSeries


@dataclass(frozen=True)
class ChartSpec:
    """A chart, as data rather than as markup.

    Sent as the content of an ``ACTIVITY_SNAPSHOT``, which the client draws
    itself. Nothing here is HTML and nothing here is interpreted as HTML: the
    server chooses the numbers and the browser chooses the DOM, which is what
    keeps a pushed visual off the sanitiser's surface entirely.

    Frozen, and **not hashable** -- ``metadata`` is part of a spec's value, so
    excluding it to buy a hash would make two specs carrying different payloads
    compare equal. That is the ordinary situation for a record with a mapping
    field; nothing here needs to be a dict key.

    **The client is the authority on shape.** It refuses a spec whose series
    disagree in length with the labels, because a chart that is subtly
    misaligned still reads as authoritative --
    [`validate`][django_ag_ui.ChartSpec.validate] catches that here instead, on
    the side that can name the offending series.
    """

    labels: tuple[str, ...]
    series: tuple[ChartSeries, ...]
    kind: ChartKind = "bar"
    title: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    """Extra keys merged into the payload, for a client that reads more than this
    package knows about.

    Copied and made read-only at construction, so a spec cannot be edited into
    an invalid one after it has been checked. Its **values** are not inspected:
    anything that Pydantic cannot serialise raises at encode time, which is
    mid-stream, after the response has already begun."""

    def __post_init__(self) -> None:
        # Coerced before validating, because a list passed in would otherwise
        # pass the length check and then be appended to afterwards -- the frozen
        # dataclass freezes the *binding*, not what it points at, so a spec
        # validated at construction could still reach the wire misaligned.
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(
            self, "series", tuple(ChartSeries(s.label, tuple(s.points)) for s in self.series)
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        self.validate()

    def validate(self) -> None:
        """Raise ``ValueError`` for a spec the client would refuse to draw.

        Checked at construction rather than at send time so a mistake surfaces
        where the data is assembled. The client refuses the same shapes and
        **silently draws nothing** -- it has no channel to complain on -- so a
        spec that gets past here reaches a user as a chart that simply is not
        there. Failing at construction names which series is wrong, on the side
        that can fix it.
        """
        if not self.labels:
            raise ValueError("a chart needs at least one label")
        if not self.series:
            raise ValueError("a chart needs at least one series")
        for label in self.labels:
            if not isinstance(label, str):
                raise ValueError(f"label {label!r} is not a string; the client refuses the spec")
        for entry in self.series:
            if len(entry.points) != len(self.labels):
                raise ValueError(
                    f"series {entry.label!r} has {len(entry.points)} points for "
                    f"{len(self.labels)} labels; every series needs one point per label"
                )
            for point in entry.points:
                validate_point(entry.label, point)

    def as_content(self) -> dict[str, Any]:
        """The activity payload the client reads."""
        # ``metadata`` spread first so it cannot shadow anything this record
        # owns. ``title`` is popped rather than merely written after, because it
        # is conditional: a spec with no title would otherwise let a stray
        # metadata key supply one, which is shadowing by omission.
        content: dict[str, Any] = {key: value for key, value in self.metadata.items()}
        content.pop("title", None)
        content.update(
            {
                "kind": self.kind,
                "labels": list(self.labels),
                "series": [{"label": s.label, "points": list(s.points)} for s in self.series],
            }
        )
        # Omitted rather than sent as null: the client treats a non-string title
        # as absent either way, and a null in the payload reads as a title that
        # failed to render.
        if self.title is not None:
            content["title"] = self.title
        return content


def validate_point(series: str, point: object) -> None:
    """Refuse a number the client will not read as one.

    ``Decimal`` is the case worth naming: a Django ``Sum`` over a
    ``DecimalField`` is the likeliest source of chart numbers in this ecosystem,
    and Pydantic serialises it as a JSON *string*. The client requires an actual
    number and drops the whole chart -- with no error on either side. Refused
    here rather than coerced, because rounding somebody's money to a float
    behind their back is the wrong favour to do them; call ``float()`` on it
    where you can see the precision you are giving up.

    ``bool`` is excluded for the usual reason it is a surprise: it is an ``int``
    to Python and would plot as 0 or 1 rather than announcing the mistake.
    """
    if isinstance(point, bool) or not isinstance(point, int | float):
        raise ValueError(
            f"series {series!r} has a point of type {type(point).__name__}; the client "
            f"reads only JSON numbers, so convert it (float(...)) before charting"
        )
    if not math.isfinite(point):
        raise ValueError(
            f"series {series!r} has a non-finite point ({point!r}); it serialises as null "
            f"and the client refuses the whole spec"
        )


__all__ = ["ChartSpec", "validate_point"]
