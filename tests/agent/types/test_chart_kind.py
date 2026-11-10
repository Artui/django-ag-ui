from __future__ import annotations

import typing

from django_ag_ui import ChartKind


def test_it_names_exactly_what_the_client_can_draw() -> None:
    # Pinned: a kind added without a drawing branch silently renders as a bar.
    assert set(typing.get_args(ChartKind)) == {"bar", "line", "pie", "scatter", "stacked"}
