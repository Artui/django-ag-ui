"""Moving one series' numbers without re-sending the chart."""

from __future__ import annotations

import pytest
from ag_ui.core import EventType

from django_ag_ui import CHART_ACTIVITY_TYPE, chart_points_delta


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


def test_a_negative_series_index_is_refused() -> None:
    # A patch path the client can never resolve: it warns and the chart simply
    # does not move, so the typo is invisible unless it is caught here.
    with pytest.raises(ValueError, match="series index"):
        chart_points_delta("c1", series=-1, points=(1.0,))


def test_points_are_checked_the_way_a_snapshot_checks_them() -> None:
    # Same hazard as a spec: a Decimal serialises to a JSON string and the
    # client refuses it, leaving a stale chart on screen with no error anywhere.
    from decimal import Decimal

    with pytest.raises(ValueError, match="reads only JSON numbers"):
        chart_points_delta("c1", points=(Decimal("1.5"),))
