"""``ChartSpec`` — the data a chart is drawn from, on the way to the browser."""

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
    """A chart, as data rather than as markup.

    Sent as the content of an ``ACTIVITY_SNAPSHOT``, which the client draws
    itself. Nothing here is HTML and nothing here is interpreted as HTML: the
    server chooses the numbers and the browser chooses the DOM, which is what
    keeps a pushed visual off the sanitiser's surface entirely.

    Frozen at the top level: ``labels``, ``series`` and the points inside them
    are copied to tuples and ``metadata`` to a read-only mapping, so a list
    handed in cannot be appended to after it has been checked. A **nested**
    structure inside ``metadata`` is still shared with the caller, and nothing
    reads it here anyway.

    Frozen, and **not usefully hashable** -- ``__hash__`` exists but raises,
    because ``metadata`` is part of a spec's value, so
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
        if self.title is not None and not isinstance(self.title, str):
            raise ValueError(
                f"title must be a string or None; got {type(self.title).__name__}, which "
                f"the client reads as no title at all"
            )
        for label in self.labels:
            if not isinstance(label, str):
                raise ValueError(f"label {label!r} is not a string; the client refuses the spec")
        if len(self.labels) > MAX_LABELS:
            # Checked separately from the point budget below, because the two
            # bounds are independent: a one-series spec of 3000 labels carries
            # 3000 points -- far inside MAX_POINTS -- and is still refused on
            # arrival, with nothing reported on either side.
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
                # Checked here rather than on ``ChartSeries`` so it is refused
                # alongside the labels it sits beside; a lazy translation object
                # is the likely culprit and it does not fail until encode time,
                # which is mid-stream with the headers already sent.
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


__all__ = ["ChartSpec"]
