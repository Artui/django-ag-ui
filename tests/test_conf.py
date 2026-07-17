from __future__ import annotations

from django.test import override_settings

from django_ag_ui.conf import get_setting


def test_missing_key_returns_the_default() -> None:
    with override_settings(DJANGO_AG_UI={}):
        assert get_setting("MODEL") is None
        assert get_setting("RETRIES", 3) == 3


def test_reads_the_configured_value() -> None:
    with override_settings(DJANGO_AG_UI={"MODEL": "anthropic:claude-sonnet-4.6"}):
        assert get_setting("MODEL") == "anthropic:claude-sonnet-4.6"


def test_absent_settings_dict_is_treated_as_empty() -> None:
    with override_settings():
        del __import__("django.conf", fromlist=["settings"]).settings.DJANGO_AG_UI
        assert get_setting("MODEL", "fallback") == "fallback"


def test_none_settings_dict_is_treated_as_empty() -> None:
    with override_settings(DJANGO_AG_UI=None):
        assert get_setting("MODEL", "fallback") == "fallback"


def test_an_explicitly_none_value_is_returned_as_none() -> None:
    """``None`` is a real configured value, not "unset" — the caller decides."""
    with override_settings(DJANGO_AG_UI={"MODEL": None}):
        assert get_setting("MODEL", "fallback") is None
