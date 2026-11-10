from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast, get_args

from django.core.exceptions import ImproperlyConfigured
from django_spec_agents import AttachmentInlineConfig
from django_spec_agents.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_spec_agents.policy.guard.types.tool_guard_config import ToolGuardConfig

from django_ag_ui.conf import get_setting
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.config.types.context_delivery import ContextDelivery
from django_ag_ui.config.types.run_context_config import RunContextConfig


def build_ag_ui_config(
    *,
    model: Any = None,
    api_key: str | None = None,
    system_prompt: str | None = None,
    model_settings: dict[str, Any] | None = None,
    retries: int | None = None,
    attachment_max_bytes: int | None = None,
    attachment_allowed_types: tuple[str, ...] | list[str] | None = None,
    attachment_inline: AttachmentInlineConfig | None = None,
    manage_system_prompt: str | None = None,
    allow_uploaded_files: bool | None = None,
    forward_reasoning: bool | None = None,
    transcription_max_bytes: int | None = None,
    transcription_allowed_types: tuple[str, ...] | list[str] | None = None,
    thread_list_limit: int | None = None,
    run_list_limit: int | None = None,
    heartbeat_seconds: float | None = None,
    approval_prompts: Mapping[str, str] | None = None,
    tool_guard: ToolGuardConfig | None = None,
    tool_failure: ToolFailureConfig | None = None,
    run_context: RunContextConfig | None = None,
) -> AGUIConfig:
    """Resolve an [`AGUIConfig`][django_ag_ui.AGUIConfig] from settings, applying overrides."""

    def pick(override: Any, key: str, default: Any) -> Any:
        if override is not None:
            return override
        return get_setting(key, default)

    return AGUIConfig(
        model=pick(model, "MODEL", None),
        api_key=pick(api_key, "API_KEY", None),
        system_prompt=pick(system_prompt, "SYSTEM_PROMPT", None),
        model_settings=pick(model_settings, "MODEL_SETTINGS", None),
        retries=pick(retries, "RETRIES", None),
        attachment_max_bytes=int(
            pick(attachment_max_bytes, "ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024)
        ),
        attachment_allowed_types=tuple(
            pick(attachment_allowed_types, "ATTACHMENT_ALLOWED_TYPES", ()) or ()
        ),
        attachment_inline=attachment_inline,
        manage_system_prompt=str(pick(manage_system_prompt, "MANAGE_SYSTEM_PROMPT", "server")),
        allow_uploaded_files=bool(pick(allow_uploaded_files, "ALLOW_UPLOADED_FILES", False)),
        forward_reasoning=bool(pick(forward_reasoning, "FORWARD_REASONING", True)),
        transcription_max_bytes=int(
            pick(transcription_max_bytes, "TRANSCRIPTION_MAX_BYTES", 25 * 1024 * 1024)
        ),
        transcription_allowed_types=tuple(
            pick(transcription_allowed_types, "TRANSCRIPTION_ALLOWED_TYPES", ()) or ()
        ),
        thread_list_limit=int(pick(thread_list_limit, "THREAD_LIST_LIMIT", 200)),
        run_list_limit=int(pick(run_list_limit, "RUN_LIST_LIMIT", 50)),
        heartbeat_seconds=float(pick(heartbeat_seconds, "HEARTBEAT_SECONDS", 15.0)),
        approval_prompts=dict(pick(approval_prompts, "APPROVAL_PROMPTS", {}) or {}),
        tool_guard=tool_guard
        if tool_guard is not None
        else _parse_tool_guard(get_setting("TOOL_GUARD")),
        tool_failure=tool_failure
        if tool_failure is not None
        else _parse_tool_failure(get_setting("TOOL_FAILURE")),
        run_context=run_context
        if run_context is not None
        else _parse_run_context(get_setting("RUN_CONTEXT")),
    )


def _parse_tool_guard(raw: Any) -> ToolGuardConfig:
    """Build a ``ToolGuardConfig`` from the ``TOOL_GUARD`` settings dict."""
    guard: dict[str, Any] = raw or {}
    return ToolGuardConfig(
        enabled=bool(guard.get("ENABLED", False)),
        exempt=frozenset(guard.get("EXEMPT", ()) or ()),
        require_approval=frozenset(guard.get("REQUIRE_APPROVAL", ()) or ()),
    )


def _parse_tool_failure(raw: Any) -> ToolFailureConfig:
    """Build a ``ToolFailureConfig`` from the ``TOOL_FAILURE`` settings dict."""
    failure: dict[str, Any] = raw or {}
    return ToolFailureConfig(
        enabled=bool(failure.get("ENABLED", True)),
        include_detail=bool(failure.get("INCLUDE_DETAIL", False)),
    )


# The ``ContextDelivery`` members, for validating the settings string. A
# ``Literal``'s arguments are the type's own definition, so reading them back
# keeps the check from drifting when a third channel is added.
_DELIVERIES: frozenset[str] = frozenset(get_args(ContextDelivery))


def _parse_run_context(raw: Any) -> RunContextConfig:
    """Build a [`RunContextConfig`][django_ag_ui.RunContextConfig] from settings."""
    run_context: dict[str, Any] = raw or {}
    delivery = str(run_context.get("DELIVERY", "instructions"))
    if delivery not in _DELIVERIES:
        raise ImproperlyConfigured(
            f"DJANGO_AG_UI['RUN_CONTEXT']['DELIVERY'] is {delivery!r}; "
            f"expected one of {sorted(_DELIVERIES)}. The two channels differ in "
            "whether client text inherits operator authority, so an unknown "
            "value is not defaulted."
        )
    return RunContextConfig(
        client_context=bool(run_context.get("CLIENT_CONTEXT", True)),
        attachment_manifest=bool(run_context.get("ATTACHMENT_MANIFEST", True)),
        max_chars=int(run_context.get("MAX_CHARS", 20000)),
        delivery=cast("ContextDelivery", delivery),
    )


__all__ = ["build_ag_ui_config"]
