from __future__ import annotations

import django_ag_ui


def test_version_is_exposed() -> None:
    assert isinstance(django_ag_ui.__version__, str)
    assert django_ag_ui.__version__.count(".") == 2
