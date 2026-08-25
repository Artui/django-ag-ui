"""One named run of numbers."""

from __future__ import annotations

from django_ag_ui import ChartSeries


def test_it_carries_a_label_and_its_points() -> None:
    series = ChartSeries("new", (1.0, 2.0))
    assert series.label == "new"
    assert series.points == (1.0, 2.0)


def test_it_is_frozen() -> None:
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        ChartSeries("new", (1.0,)).label = "other"  # type: ignore[misc]


def test_the_length_agreement_is_not_its_business() -> None:
    # It has no idea how many labels the chart has, so a series of any length
    # constructs; ChartSpec is the only thing that can check the pairing.
    assert len(ChartSeries("solo", (1.0, 2.0, 3.0)).points) == 3
