from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django_spec_agents import AttachmentInlineConfig
from django_spec_agents.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_spec_agents.policy.guard.types.tool_guard_config import ToolGuardConfig

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.run_context_config import RunContextConfig


def test_defaults_when_unconfigured() -> None:
    with override_settings(DJANGO_AG_UI={}):
        config = build_ag_ui_config()
    assert config.model is None
    assert config.retries is None
    assert config.thread_list_limit == 200
    assert config.run_list_limit == 50
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
    """Used instead of AGUIConfig directly: an override for one field keeps the other settings."""
    with override_settings(DJANGO_AG_UI={"RETRIES": 5, "THREAD_LIST_LIMIT": 9}):
        config = build_ag_ui_config(retries=1)
    assert config.retries == 1
    assert config.thread_list_limit == 9


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


def test_tool_failure_defaults_to_on_with_no_settings() -> None:
    """The one policy whose absent-settings answer is "on"."""
    with override_settings(DJANGO_AG_UI={}):
        config = build_ag_ui_config()
    assert config.tool_failure.enabled is True
    assert config.tool_failure.include_detail is False


def test_tool_failure_is_parsed_from_the_settings_dict() -> None:
    with override_settings(
        DJANGO_AG_UI={"TOOL_FAILURE": {"ENABLED": False, "INCLUDE_DETAIL": True}}
    ):
        config = build_ag_ui_config()
    assert config.tool_failure.enabled is False
    assert config.tool_failure.include_detail is True


def test_an_empty_tool_failure_dict_reads_as_the_defaults() -> None:
    # "Configured but empty" must not read as "disabled".
    with override_settings(DJANGO_AG_UI={"TOOL_FAILURE": {}}):
        config = build_ag_ui_config()
    assert config.tool_failure.enabled is True


def test_an_explicit_tool_failure_wins() -> None:
    with override_settings(DJANGO_AG_UI={"TOOL_FAILURE": {"ENABLED": True}}):
        config = build_ag_ui_config(tool_failure=ToolFailureConfig(enabled=False))
    assert config.tool_failure.enabled is False


def test_run_context_defaults_to_delivering_both_sources() -> None:
    """The other policy whose absent-settings answer is "on"."""
    with override_settings(DJANGO_AG_UI={}):
        config = build_ag_ui_config()
    assert config.run_context == RunContextConfig(
        client_context=True, attachment_manifest=True, max_chars=20000
    )


def test_one_run_context_flag_flips_without_disturbing_the_others() -> None:
    with override_settings(DJANGO_AG_UI={"RUN_CONTEXT": {"CLIENT_CONTEXT": False}}):
        config = build_ag_ui_config()
    assert config.run_context.client_context is False
    assert config.run_context.attachment_manifest is True
    assert config.run_context.max_chars == 20000


def test_the_run_context_ceiling_is_parsed_through() -> None:
    with override_settings(DJANGO_AG_UI={"RUN_CONTEXT": {"MAX_CHARS": 10}}):
        config = build_ag_ui_config()
    assert config.run_context.max_chars == 10


def test_an_explicit_run_context_wins() -> None:
    with override_settings(DJANGO_AG_UI={"RUN_CONTEXT": {"CLIENT_CONTEXT": True}}):
        config = build_ag_ui_config(
            run_context=RunContextConfig(
                client_context=False, attachment_manifest=False, max_chars=5
            )
        )
    assert config.run_context.client_context is False
    assert config.run_context.max_chars == 5


def test_the_delivery_channel_defaults_to_instructions() -> None:
    """What every release before the channel existed did."""
    with override_settings(DJANGO_AG_UI={}):
        assert build_ag_ui_config().run_context.delivery == "instructions"


def test_the_delivery_channel_is_parsed_through() -> None:
    with override_settings(DJANGO_AG_UI={"RUN_CONTEXT": {"DELIVERY": "tool"}}):
        assert build_ag_ui_config().run_context.delivery == "tool"


def test_an_unknown_delivery_channel_raises_rather_than_defaulting() -> None:
    """The channels differ in whether client text inherits authority: refuse a typo at startup."""
    with (
        override_settings(DJANGO_AG_UI={"RUN_CONTEXT": {"DELIVERY": "instrctions"}}),
        pytest.raises(ImproperlyConfigured, match="instrctions"),
    ):
        build_ag_ui_config()


def test_attachment_inline_defaults_to_the_substrates_own() -> None:
    # None, not a copy of the substrate's defaults, so the default cannot drift out of sync here.
    with override_settings(DJANGO_AG_UI={}):
        assert build_ag_ui_config().attachment_inline is None


def test_attachment_inline_is_configurable_alongside_the_upload_cap() -> None:
    """The two budgets must be settable together."""
    inline = AttachmentInlineConfig(max_bytes=8 * 1024 * 1024)
    with override_settings(DJANGO_AG_UI={"ATTACHMENT_MAX_BYTES": 8 * 1024 * 1024}):
        config = build_ag_ui_config(attachment_inline=inline)

    assert config.attachment_inline is inline
    assert config.attachment_max_bytes <= config.attachment_inline.max_bytes


def test_attachment_inlining_can_be_switched_off() -> None:
    # A consumer bounding the bytes re-sent per request needs the lever the substrate documents.
    off = AttachmentInlineConfig(media_types=frozenset())
    with override_settings(DJANGO_AG_UI={}):
        config = build_ag_ui_config(attachment_inline=off)
    assert config.attachment_inline is not None
    assert config.attachment_inline.media_types == frozenset()


def test_the_run_list_ceiling_is_configurable() -> None:
    """A per-endpoint ceiling, like every other limit here."""
    with override_settings(DJANGO_AG_UI={"RUN_LIST_LIMIT": 5}):
        assert build_ag_ui_config().run_list_limit == 5
    assert build_ag_ui_config(run_list_limit=7).run_list_limit == 7


def test_the_heartbeat_interval_is_configurable_and_on_by_default() -> None:
    """On by default, because the failure it prevents is invisible from here."""
    with override_settings(DJANGO_AG_UI={}):
        assert build_ag_ui_config().heartbeat_seconds == 15.0
    with override_settings(DJANGO_AG_UI={"HEARTBEAT_SECONDS": 5}):
        assert build_ag_ui_config().heartbeat_seconds == 5.0
    assert build_ag_ui_config(heartbeat_seconds=2.5).heartbeat_seconds == 2.5
    with override_settings(DJANGO_AG_UI={"HEARTBEAT_SECONDS": 0}):
        assert build_ag_ui_config().heartbeat_seconds == 0
    assert build_ag_ui_config(heartbeat_seconds=0).heartbeat_seconds == 0
