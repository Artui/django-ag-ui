from __future__ import annotations

from django.test import override_settings

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.policy.guard.types.tool_guard_config import ToolGuardConfig


def test_defaults_when_unconfigured() -> None:
    with override_settings(DJANGO_AG_UI={}):
        config = build_ag_ui_config()
    assert config.model is None
    assert config.retries is None
    assert config.thread_list_limit == 200
    assert config.forward_reasoning is True
    assert config.manage_system_prompt == "server"
    assert config.tool_guard.enabled is False


def test_settings_supply_the_defaults() -> None:
    """The single-endpoint on-ramp is unchanged: configure settings, pass nothing."""
    with override_settings(DJANGO_AG_UI={"RETRIES": 5, "THREAD_LIST_LIMIT": 9}):
        config = build_ag_ui_config()
    assert config.retries == 5
    assert config.thread_list_limit == 9


def test_overrides_win_over_settings() -> None:
    with override_settings(DJANGO_AG_UI={"RETRIES": 5}):
        config = build_ag_ui_config(retries=1)
    assert config.retries == 1


def test_overrides_layer_over_settings_rather_than_replacing_them() -> None:
    """The reason to call this instead of constructing AGUIConfig directly: an
    override for one field must not discard the project's other settings."""
    with override_settings(DJANGO_AG_UI={"RETRIES": 5, "THREAD_LIST_LIMIT": 9}):
        config = build_ag_ui_config(retries=1)
    assert config.retries == 1
    assert config.thread_list_limit == 9  # still the project's value


def test_tool_guard_is_parsed_from_the_settings_dict() -> None:
    with override_settings(DJANGO_AG_UI={"TOOL_GUARD": {"ENABLED": True, "EXEMPT": ["safe_tool"]}}):
        config = build_ag_ui_config()
    assert config.tool_guard.enabled is True
    assert config.tool_guard.exempt == frozenset({"safe_tool"})


def test_an_explicit_tool_guard_wins() -> None:
    with override_settings(DJANGO_AG_UI={"TOOL_GUARD": {"ENABLED": True}}):
        config = build_ag_ui_config(tool_guard=ToolGuardConfig(enabled=False))
    assert config.tool_guard.enabled is False


def test_two_endpoints_can_hold_different_scalars() -> None:
    """The point: read per request these could only ever be global."""
    with override_settings(DJANGO_AG_UI={"RETRIES": 5}):
        internal = build_ag_ui_config(retries=1, thread_list_limit=10)
        public = build_ag_ui_config(retries=9, thread_list_limit=500)
    assert (internal.retries, internal.thread_list_limit) == (1, 10)
    assert (public.retries, public.thread_list_limit) == (9, 500)
