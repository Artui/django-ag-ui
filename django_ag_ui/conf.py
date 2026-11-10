from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings

_SETTING_NAME = "DJANGO_AG_UI"


def get_setting(name: str, default: Any = None) -> Any:
    """Read one key out of the ``DJANGO_AG_UI`` settings dict."""
    raw: dict[str, Any] = getattr(django_settings, _SETTING_NAME, {}) or {}
    return raw.get(name, default)


__all__ = ["get_setting"]
