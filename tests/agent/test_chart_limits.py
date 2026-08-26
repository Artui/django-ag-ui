"""The bounds both sides of the wire have to agree on.

These exist because the client refuses a payload outside them and has **no
channel to say so**. A producer that does not know the same numbers ships
charts that vanish on arrival, which is why the limits live in one module and
are asserted here rather than left implicit in each caller.
"""

from __future__ import annotations

import pytest

from django_ag_ui.agent import chart_limits
from django_ag_ui.agent.chart_limits import (
    MAX_LABELS,
    MAX_MAGNITUDE,
    MAX_POINTS,
    validate_point,
)


def test_an_ordinary_number_passes() -> None:
    validate_point("series 'x'", 1)
    validate_point("series 'x'", -2.5)
    validate_point("series 'x'", 0)


def test_a_decimal_is_refused_rather_than_coerced() -> None:
    from decimal import Decimal

    # The mistake a Django app makes first: a Sum over a DecimalField
    # serialises as a JSON string, which the client will not read as a number.
    with pytest.raises(ValueError, match="reads only JSON numbers"):
        validate_point("series 'x'", Decimal("1.5"))


def test_a_bool_is_refused_because_python_calls_it_an_int() -> None:
    with pytest.raises(ValueError, match="type bool"):
        validate_point("series 'x'", True)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_point_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        validate_point("series 'x'", bad)


def test_a_magnitude_over_the_clients_bound_is_refused() -> None:
    """Finite is not sufficient, which is the subtle half of this rule.

    Two finite extremes still give an infinite *range*, and the client divides
    by that range to scale -- yielding NaN in every coordinate. It bounds
    magnitude for that reason, and a producer that does not is shipping charts
    that arrive and are discarded.
    """
    validate_point("series 'x'", MAX_MAGNITUDE)
    with pytest.raises(ValueError, match="over the client's"):
        validate_point("series 'x'", MAX_MAGNITUDE * 10)
    with pytest.raises(ValueError, match="over the client's"):
        validate_point("series 'x'", -MAX_MAGNITUDE * 10)


def test_the_message_names_where_the_bad_point_came_from() -> None:
    # The caller has one series among several; "a point is wrong" is not enough
    # to act on.
    with pytest.raises(ValueError, match="series 'revenue'"):
        validate_point("series 'revenue'", None)


def test_the_limits_match_the_documented_client_values() -> None:
    """Every bound the component enforces, pinned to the component's own number.

    Sourced from ``MAX_MAGNITUDE`` / ``MAX_POINTS`` / ``MAX_LABELS`` in
    ``src/ui/chart_spec_from.ts`` of ``@artooi/ag-ui-web-component``, where they
    are applied together in one refusal:
    ``series.length * labels.length > MAX_POINTS || labels.length > MAX_LABELS``.

    Pinned rather than merely referenced. If the component's own limits move,
    this is the test that should fail and send someone to the other repo -- a
    mismatch is otherwise invisible in both suites, because each passes its own
    and only a payload crossing the gap between them fails.

    **Count them, too.** Mirroring two of the client's three bounds is what left
    ``MAX_LABELS`` unenforced here for a release: each pinned value looked
    correct, and the missing one had nothing to be unequal to. The count below
    is what fails when the component grows a fourth.
    """
    assert MAX_MAGNITUDE == 1e15
    assert MAX_POINTS == 20_000
    assert MAX_LABELS == 2_000
    assert sorted(chart_limits.__all__) == [
        "MAX_LABELS",
        "MAX_MAGNITUDE",
        "MAX_POINTS",
        "validate_point",
    ]
