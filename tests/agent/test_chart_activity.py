from __future__ import annotations

import json

from ag_ui.core import EventType
from ag_ui.encoder import EventEncoder

from django_ag_ui import (
    CHART_ACTIVITY_TYPE,
    ChartSeries,
    ChartSpec,
    chart_activity,
)

SPEC = ChartSpec(labels=("a", "b"), series=(ChartSeries("one", (1.0, 2.0)),))


def test_the_encoded_event_carries_the_names_the_client_matches_on() -> None:
    """Asserted on the wire, not on the object."""
    encoded = EventEncoder().encode(chart_activity(SPEC, chart_id="c1"))
    payload = json.loads(encoded.removeprefix("data: ").strip())

    assert payload["type"] == "ACTIVITY_SNAPSHOT"
    assert payload["activityType"] == "chart"
    assert payload["messageId"] == "c1"
    assert payload["replace"] is True
    assert payload["content"]["series"] == [{"label": "one", "points": [1.0, 2.0]}]


def test_the_points_reach_the_wire_as_json_numbers() -> None:
    # The client reads only JSON numbers; a string or null silently costs the whole chart.
    payload = json.loads(EventEncoder().encode(chart_activity(SPEC)).removeprefix("data: ").strip())
    for point in payload["content"]["series"][0]["points"]:
        assert isinstance(point, float | int)


def test_it_is_a_vanilla_activity_snapshot() -> None:
    # ACTIVITY_SNAPSHOT, not CUSTOM, so the wire stays vanilla AG-UI.
    event = chart_activity(SPEC)
    assert event.type == EventType.ACTIVITY_SNAPSHOT
    assert event.activity_type == CHART_ACTIVITY_TYPE
    assert event.content == SPEC.as_content()


def test_each_chart_gets_its_own_id_unless_one_is_given() -> None:
    assert chart_activity(SPEC).message_id != chart_activity(SPEC).message_id


def test_repeating_an_id_replaces_the_chart_rather_than_adding_one() -> None:
    # The identity is the chart's, not the event's: a redrawn chart is one chart moving.
    first = chart_activity(SPEC, chart_id="c1")
    second = chart_activity(SPEC, chart_id="c1")
    assert first.message_id == second.message_id == "c1"
