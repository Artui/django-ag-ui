from __future__ import annotations

import json
from typing import Any

import pytest
from ag_ui.core import ActivityDeltaEvent, EventType
from ag_ui.encoder import EventEncoder

from django_ag_ui import CHART_ACTIVITY_TYPE, ChartSeries, ChartSpec, chart_points_delta


def _served_patch(delta: ActivityDeltaEvent) -> list[dict[str, Any]]:
    """The patch as the browser receives it, read off the encoder's own frame."""
    frame = EventEncoder().encode(delta)
    return json.loads(frame.removeprefix("data: "))["patch"]


def test_a_delta_moves_one_series_without_re_sending_the_chart() -> None:
    delta = chart_points_delta("c1", points=[3.0, 4.0])
    assert delta.type == EventType.ACTIVITY_DELTA
    assert delta.message_id == "c1"
    assert delta.activity_type == CHART_ACTIVITY_TYPE
    assert _served_patch(delta) == [
        {"op": "replace", "path": "/series/0/points", "value": [3.0, 4.0]}
    ]


def test_a_delta_can_name_a_series_other_than_the_first() -> None:
    delta = chart_points_delta("c1", series=2, points=(9.0,))
    assert _served_patch(delta)[0]["path"] == "/series/2/points"


def test_a_delta_accepts_a_tuple_and_sends_a_list() -> None:
    delta = chart_points_delta("c1", points=(1.0, 2.0))
    assert _served_patch(delta)[0]["value"] == [1.0, 2.0]
    # Only the model shows the encoder turned a tuple into an array, not the frame.
    assert delta.model_dump()["patch"][0]["value"] == [1.0, 2.0]


def test_a_negative_series_index_is_refused() -> None:
    # A patch path the client cannot resolve warns and never moves the chart; catch the typo here.
    with pytest.raises(ValueError, match="series index"):
        chart_points_delta("c1", series=-1, points=(1.0,))


def test_points_are_checked_the_way_a_snapshot_checks_them() -> None:
    # A Decimal serialises to a JSON string the client refuses, leaving a stale chart silently.
    from decimal import Decimal

    with pytest.raises(ValueError, match="reads only JSON numbers"):
        chart_points_delta("c1", points=(Decimal("1.5"),))


def test_an_empty_points_array_is_refused() -> None:
    """It applies cleanly and then breaks the chart, which is the worst shape."""
    with pytest.raises(ValueError, match="at least one point"):
        chart_points_delta("c1", points=())


def test_a_magnitude_the_client_refuses_is_caught_here_too() -> None:
    # Snapshot and delta paths must agree on a bound; one enforced on one path is a gap.
    with pytest.raises(ValueError, match="over the client's"):
        chart_points_delta("c1", points=(1e300,))


def _spec() -> ChartSpec:
    return ChartSpec(
        labels=("mon", "tue", "wed"),
        series=(ChartSeries("new", (1.0, 2.0, 3.0)), ChartSeries("returning", (4.0, 5.0, 6.0))),
    )


def test_a_declared_spec_refuses_a_points_array_of_the_wrong_length() -> None:
    """The failure the helper could not see until the caller declared the shape."""
    with pytest.raises(ValueError, match="2 points for a chart of 3 labels"):
        chart_points_delta("c1", points=(7.0, 8.0), spec=_spec())


def test_a_declared_spec_refuses_a_series_index_past_the_end() -> None:
    # A patch path that resolves to nothing warns in a console nobody is watching.
    with pytest.raises(ValueError, match="names series 5 of a chart that has 2"):
        chart_points_delta("c1", series=5, points=(7.0, 8.0, 9.0), spec=_spec())


def test_a_delta_matching_the_declared_spec_is_built_as_usual() -> None:
    delta = chart_points_delta("c1", series=1, points=(7.0, 8.0, 9.0), spec=_spec())
    assert _served_patch(delta) == [
        {"op": "replace", "path": "/series/1/points", "value": [7.0, 8.0, 9.0]}
    ]


def test_a_caller_that_declares_nothing_behaves_exactly_as_before() -> None:
    # The guard is opt-in: without a spec the helper cannot know the shape, so a bad delta goes out.
    delta = chart_points_delta("c1", points=(7.0, 8.0))
    assert _served_patch(delta)[0]["value"] == [7.0, 8.0]
