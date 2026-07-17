from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings

_SETTING_NAME = "DJANGO_AG_UI"


def get_setting(name: str, default: Any = None) -> Any:
    """Read one key out of the ``DJANGO_AG_UI`` settings dict.

    The package's only settings primitive. Settings are a **default source**,
    read once when a server / store / backend is constructed — never on the
    request path, which is what lets two endpoints in one project differ.

    There is deliberately no ``get_settings()`` returning a whole snapshot any
    more: a process-global config object is exactly what made two AG-UI
    endpoints impossible to tell apart. Use
    :func:`~django_ag_ui.config.build_ag_ui_config.build_ag_ui_config` for the
    resolved per-endpoint scalars.
    """
    raw: dict[str, Any] = getattr(django_settings, _SETTING_NAME, {}) or {}
    return raw.get(name, default)


__all__ = ["get_setting"]
