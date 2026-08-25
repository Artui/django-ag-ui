"""A chart's data, checked where it is assembled rather than where it is drawn."""

from __future__ import annotations

import pytest

from django_ag_ui import ChartSeries, ChartSpec


def _spec(**over: object) -> ChartSpec:
    base: dict[str, object] = {
        "labels": ("a", "b"),
        "series": (ChartSeries("one", (1.0, 2.0)),),
    }
    return ChartSpec(**{**base, **over})  # type: ignore[arg-type]


def test_a_well_formed_spec_becomes_the_payload_the_client_reads() -> None:
    content = _spec(kind="line", title="T").as_content()
    assert content == {
        "kind": "line",
        "labels": ["a", "b"],
        "series": [{"label": "one", "points": [1.0, 2.0]}],
        "title": "T",
    }


def test_the_default_kind_is_bar() -> None:
    assert _spec().as_content()["kind"] == "bar"


def test_a_missing_title_is_omitted_rather_than_sent_as_null() -> None:
    # A null in the payload reads as a title that failed to render.
    assert "title" not in _spec().as_content()


def test_metadata_rides_along_without_being_inspected() -> None:
    content = _spec(metadata={"unit": "signups"}).as_content()
    assert content["unit"] == "signups"


def test_metadata_cannot_shadow_the_fields_the_client_needs() -> None:
    # Merged first, so a stray key cannot replace the labels or the series.
    content = _spec(metadata={"labels": ["x"], "kind": "pie"}).as_content()
    assert content["labels"] == ["a", "b"]
    assert content["kind"] == "bar"


def test_a_spec_with_no_labels_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one label"):
        _spec(labels=())


def test_a_spec_with_no_series_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one series"):
        _spec(series=())


def test_a_series_that_disagrees_with_the_labels_names_itself() -> None:
    # The client refuses the same shape and silently draws nothing; failing here
    # says which series is wrong, on the side that can fix it.
    with pytest.raises(ValueError, match="'short' has 1 points for 2 labels"):
        _spec(series=(ChartSeries("short", (1.0,)),))
