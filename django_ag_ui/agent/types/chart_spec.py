from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from django_ag_ui.agent.chart_limits import MAX_LABELS, MAX_POINTS, validate_point
from django_ag_ui.agent.types.chart_kind import ChartKind
from django_ag_ui.agent.types.chart_series import ChartSeries


@dataclass(frozen=True)
class ChartSpec:
    """A chart, as data rather than as markup."""

    labels: tuple[str, ...]
    series: tuple[ChartSeries, ...]
    kind: ChartKind = "bar"
    title: str | None = None
    """Shown above the chart. Anything but a string is refused rather than sent:
    the client treats a non-string title as absent, so it would vanish quietly."""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    """Extra keys merged into the payload, for a client that reads more than this
    package knows about.

    Copied and made read-only at construction, so a spec cannot be edited into
    an invalid one after it has been checked. Its **values** are not inspected:
    anything that Pydantic cannot serialise raises at encode time, which is
    mid-stream, after the response has already begun."""

    def __post_init__(self) -> None:
        # Coerced before validating: a frozen dataclass freezes the binding, not its contents.
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(
            self, "series", tuple(ChartSeries(s.label, tuple(s.points)) for s in self.series)
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        self.validate()

    def validate(self) -> None:
        """Raise ``ValueError`` for a spec the client would refuse to draw."""
        if not self.labels:
            raise ValueError("a chart needs at least one label")
        if not self.series:
            raise ValueError("a chart needs at least one series")
        if self.title is not None and not isinstance(self.title, str):
            raise ValueError(
                f"title must be a string or None; got {type(self.title).__name__}, which "
                f"the client reads as no title at all"
            )
        for label in self.labels:
            if not isinstance(label, str):
                raise ValueError(f"label {label!r} is not a string; the client refuses the spec")
        if len(self.labels) > MAX_LABELS:
            raise ValueError(
                f"this chart carries {len(self.labels)} labels, over the client's "
                f"{MAX_LABELS} limit; it would be refused on arrival, and drawing one "
                f"axis label per entry would block the browser on every reload of the "
                f"conversation"
            )
        total = len(self.labels) * len(self.series)
        if total > MAX_POINTS:
            raise ValueError(
                f"this chart carries {total} points, over the client's {MAX_POINTS} limit; "
                f"it would be refused on arrival, and drawing one that large would block "
                f"the browser on every reload of the conversation"
            )
        for entry in self.series:
            if not isinstance(entry.label, str):
                raise ValueError(
                    f"series label {entry.label!r} is not a string (got "
                    f"{type(entry.label).__name__}); wrap a lazy translation in str() "
                    f"before charting, or it fails when the event is serialised"
                )
            if len(entry.points) != len(self.labels):
                raise ValueError(
                    f"series {entry.label!r} has {len(entry.points)} points for "
                    f"{len(self.labels)} labels; every series needs one point per label"
                )
            for point in entry.points:
                validate_point(f"series {entry.label!r}", point)

    def as_content(self) -> dict[str, Any]:
        """The activity payload the client reads."""
        content: dict[str, Any] = {key: value for key, value in self.metadata.items()}
        content.pop("title", None)
        content.update(
            {
                "kind": self.kind,
                "labels": list(self.labels),
                "series": [{"label": s.label, "points": list(s.points)} for s in self.series],
            }
        )
        if self.title is not None:
            content["title"] = self.title
        return content


__all__ = ["ChartSpec"]
