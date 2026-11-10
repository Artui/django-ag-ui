from __future__ import annotations

from decimal import Decimal

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
    # The client silently draws nothing for this shape; failing here names the wrong series.
    with pytest.raises(ValueError, match="'short' has 1 points for 2 labels"):
        _spec(series=(ChartSeries("short", (1.0,)),))


def test_a_non_string_label_is_refused() -> None:
    # The client drops the whole spec on a non-string label: a stray int costs the chart.
    with pytest.raises(ValueError, match="is not a string"):
        _spec(labels=("a", 2))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_point_is_refused(bad: float) -> None:
    # Serialises as null, which the client reads as "not a number" and refuses.
    with pytest.raises(ValueError, match="non-finite point"):
        _spec(series=(ChartSeries("s", (bad, 1.0)),))


def test_a_decimal_point_is_refused_rather_than_coerced() -> None:
    """The case a Django app hits first, and the one that fails most quietly."""
    with pytest.raises(ValueError, match="reads only JSON numbers"):
        _spec(series=(ChartSeries("s", (Decimal("1.5"), Decimal("2"))),))


def test_a_bool_point_is_refused() -> None:
    # A bool is an int to Python and would plot as 0 or 1 rather than saying so.
    with pytest.raises(ValueError, match="type bool"):
        _spec(series=(ChartSeries("s", (True, 1.0)),))


def test_a_list_of_labels_is_frozen_at_construction() -> None:
    # Freezing the binding does not freeze the list: appending after the check misaligns.
    spec = ChartSpec(labels=["a", "b"], series=[ChartSeries("s", [1.0, 2.0])])
    assert isinstance(spec.labels, tuple)
    assert isinstance(spec.series[0].points, tuple)


def test_metadata_is_copied_and_read_only() -> None:
    supplied = {"unit": "signups"}
    spec = _spec(metadata=supplied)
    supplied["unit"] = "changed"
    assert spec.as_content()["unit"] == "signups"
    with pytest.raises(TypeError):
        spec.metadata["unit"] = "later"  # type: ignore[index]


def test_metadata_cannot_supply_a_title_the_spec_does_not_have() -> None:
    # Shadowing by omission: a stray key would slip through on a spec with no title of its own.
    assert "title" not in _spec(metadata={"title": "sneaky"}).as_content()
    assert _spec(title="real", metadata={"title": "sneaky"}).as_content()["title"] == "real"


def test_a_spec_over_the_clients_point_ceiling_is_refused() -> None:
    """Refused rather than sent and discarded."""
    labels = tuple(str(i) for i in range(1_000))
    series = tuple(ChartSeries(f"s{i}", (1.0,) * 1_000) for i in range(21))
    with pytest.raises(ValueError, match="over the client's 20000 limit"):
        ChartSpec(labels=labels, series=series)


def test_a_spec_over_the_clients_label_ceiling_is_refused() -> None:
    """The bound the server used to be missing, and the one nothing reported."""
    labels = tuple(str(i) for i in range(2_500))
    series = (ChartSeries("count", tuple(float(i) for i in range(2_500))),)
    with pytest.raises(ValueError, match="over the client's 2000 limit"):
        ChartSpec(labels=labels, series=series)


def test_a_spec_at_the_label_ceiling_is_accepted() -> None:
    # The bound is inclusive: the client refuses more than MAX_LABELS, so exactly MAX_LABELS passes.
    labels = tuple(str(i) for i in range(2_000))
    ChartSpec(labels=labels, series=(ChartSeries("count", (1.0,) * 2_000),))


def test_a_lazy_translation_as_a_series_label_is_refused() -> None:
    """The failure this prevents happens at the worst possible moment."""
    from django.utils.translation import gettext_lazy as _

    with pytest.raises(ValueError, match="is not a string"):
        _spec(series=(ChartSeries(_("Signups"), (1.0, 2.0)),))


def test_a_non_string_title_is_refused_rather_than_silently_dropped() -> None:
    # The client reads a non-string title as no title, losing the heading with nothing reported.
    with pytest.raises(ValueError, match="title must be a string"):
        _spec(title=42)
