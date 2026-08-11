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
    # ⭐ ``None`` and ``True`` resolve alike on purpose: unstated behaves as
    # exempt, and the two are kept apart only so the agent view's guard can warn
    # on the silence without warning on a deliberate answer. Nothing downstream
    # of this function needs the distinction.
    assert resolve_csrf_exempt(stated) is resolved
