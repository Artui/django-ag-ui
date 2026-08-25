"""The vocabulary the client draws."""

from __future__ import annotations

import typing

from django_ag_ui import ChartKind


def test_it_names_exactly_what_the_client_can_draw() -> None:
    # Pinned rather than merely documented: adding a kind here without adding a
    # drawing branch in the component ships a name that silently renders as a
    # bar, which looks like a bug in the data rather than a missing feature.
    assert set(typing.get_args(ChartKind)) == {"bar", "line", "pie", "scatter", "stacked"}
