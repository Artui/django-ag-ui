from __future__ import annotations

import pytest

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt


@pytest.mark.parametrize(
    ("stated", "resolved"),
    [(None, True), (True, True), (False, False)],
    ids=["unstated-is-exempt", "explicit-exempt", "explicit-enforced"],
)
def test_resolves_the_tristate_to_the_flag_django_reads(
    stated: bool | None, resolved: bool
) -> None:
    # None and True resolve alike; they are kept apart so the view can warn on silence only.
    assert resolve_csrf_exempt(stated) is resolved
