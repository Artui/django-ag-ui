"""Pushing a chart to the client, and moving one already there."""

from __future__ import annotations

from ag_ui.core import EventType

from django_ag_ui import (
    CHART_ACTIVITY_TYPE,
    ChartSeries,
    ChartSpec,
    chart_activity,
    chart_points_delta,
)

SPEC = ChartSpec(labels=("a", "b"), series=(ChartSeries("one", (1.0, 2.0)),))


def test_it_is_a_vanilla_activity_snapshot() -> None:
    # An ACTIVITY_SNAPSHOT rather than a CUSTOM event, so the wire stays vanilla
    # AG-UI and ours is not a privileged client. A client that does not know the
    # name ignores it.
    event = chart_activity(SPEC)
    assert event.type == EventType.ACTIVITY_SNAPSHOT
    assert event.activity_type == CHART_ACTIVITY_TYPE
    assert event.content == SPEC.as_content()


def test_each_chart_gets_its_own_id_unless_one_is_given() -> None:
    assert chart_activity(SPEC).message_id != chart_activity(SPEC).message_id


def test_repeating_an_id_replaces_the_chart_rather_than_adding_one() -> None:
    # The identity is the chart's, not the event's: a chart redrawn as a
    # computation advances is one chart moving, not a stack of copies.
    first = chart_activity(SPEC, chart_id="c1")
    second = chart_activity(SPEC, chart_id="c1")
    assert first.message_id == second.message_id == "c1"
    assert second.replace is True


def test_a_delta_moves_one_series_without_re_sending_the_chart() -> None:
    delta = chart_points_delta("c1", points=[3.0, 4.0])
    assert delta.type == EventType.ACTIVITY_DELTA
    assert delta.message_id == "c1"
    assert delta.activity_type == CHART_ACTIVITY_TYPE
    assert delta.patch == [{"op": "replace", "path": "/series/0/points", "value": [3.0, 4.0]}]


def test_a_delta_can_name_a_series_other_than_the_first() -> None:
    delta = chart_points_delta("c1", series=2, points=(9.0,))
    assert delta.patch[0]["path"] == "/series/2/points"


def test_a_delta_accepts_a_tuple_and_sends_a_list() -> None:
    # JSON has no tuples, and a patch value has to survive serialisation.
    assert chart_points_delta("c1", points=(1.0, 2.0)).patch[0]["value"] == [1.0, 2.0]
