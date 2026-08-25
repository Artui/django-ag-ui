"""``ChartSpec`` — the data a chart is drawn from, on the way to the browser."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ChartKind = Literal["bar", "line", "pie", "scatter", "stacked"]
"""How the client draws a spec. An unrecognised kind is drawn as ``bar``."""


@dataclass(frozen=True)
class ChartSeries:
    """One named series of numbers, one per label."""

    label: str
    points: tuple[float, ...]


@dataclass(frozen=True)
class ChartSpec:
    """A chart, as data rather than as markup.

    Sent as the content of an ``ACTIVITY_SNAPSHOT``, which the client draws
    itself. Nothing here is HTML and nothing here is interpreted as HTML: the
    server chooses the numbers and the browser chooses the DOM, which is what
    keeps a pushed visual off the sanitiser's surface entirely.

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
    metadata: dict[str, Any] = field(default_factory=dict)
    """Extra keys merged into the payload, for a client that reads more than
    this package knows about. Never inspected here."""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Raise ``ValueError`` for a spec the client would refuse to draw.

        Checked at construction rather than at send time so a mistake surfaces
        where the data is assembled. The client refuses the same shapes and
        silently draws nothing; failing here names which series is wrong.
        """
        if not self.labels:
            raise ValueError("a chart needs at least one label")
        if not self.series:
            raise ValueError("a chart needs at least one series")
        for entry in self.series:
            if len(entry.points) != len(self.labels):
                raise ValueError(
                    f"series {entry.label!r} has {len(entry.points)} points for "
                    f"{len(self.labels)} labels; every series needs one point per label"
                )

    def as_content(self) -> dict[str, Any]:
        """The activity payload the client reads."""
        content: dict[str, Any] = {
            **self.metadata,
            "kind": self.kind,
            "labels": list(self.labels),
            "series": [{"label": s.label, "points": list(s.points)} for s in self.series],
        }
        # Omitted rather than sent as null: the client treats a non-string title
        # as absent either way, and a null in the payload reads as a title that
        # failed to render.
        if self.title is not None:
            content["title"] = self.title
        return content


__all__ = ["ChartKind", "ChartSeries", "ChartSpec"]
