from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings

from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.policy.guard.types.tool_guard_config import ToolGuardConfig

_SETTING_NAME = "DJANGO_AG_UI"


def build_ag_ui_config(
    *,
    model: Any = None,
    api_key: str | None = None,
    system_prompt: str | None = None,
    model_settings: dict[str, Any] | None = None,
    retries: int | None = None,
    attachment_max_bytes: int | None = None,
    attachment_allowed_types: tuple[str, ...] | list[str] | None = None,
    manage_system_prompt: str | None = None,
    allow_uploaded_files: bool | None = None,
    forward_reasoning: bool | None = None,
    transcription_max_bytes: int | None = None,
    transcription_allowed_types: tuple[str, ...] | list[str] | None = None,
    thread_list_limit: int | None = None,
    tool_guard: ToolGuardConfig | None = None,
    allow_anonymous: bool | None = None,
) -> AGUIConfig:
    """Resolve an :class:`AGUIConfig` from ``DJANGO_AG_UI``, applying overrides.

    The single place the scalar settings are read. :class:`AGUIServer` calls this
    once in ``__init__``; nothing reads these settings per request, which is what
    lets two endpoints in one project hold different values.

    Every argument is ``None`` by default, meaning "take it from settings". Pass
    one to override just that field for this endpoint::

        AGUIServer(registry, config=build_ag_ui_config(retries=3))

    Use this rather than constructing :class:`AGUIConfig` directly — it is what
    layers your overrides *over* the project's settings instead of discarding
    them.
    """
    raw: dict[str, Any] = getattr(django_settings, _SETTING_NAME, {}) or {}

    def pick(override: Any, key: str, default: Any) -> Any:
        if override is not None:
            return override
        return raw.get(key, default)

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
        tool_guard=tool_guard
        if tool_guard is not None
        else _parse_tool_guard(raw.get("TOOL_GUARD")),
        allow_anonymous=bool(pick(allow_anonymous, "ALLOW_ANONYMOUS", False)),
    )


def _parse_tool_guard(raw: Any) -> ToolGuardConfig:
    """Build a :class:`ToolGuardConfig` from the ``TOOL_GUARD`` settings dict.

    Absent / falsy → a disabled config (the default). Names are normalised to
    ``frozenset`` so lookups in the capability are O(1) and order-insensitive.
    """
    guard: dict[str, Any] = raw or {}
    return ToolGuardConfig(
        enabled=bool(guard.get("ENABLED", False)),
        exempt=frozenset(guard.get("EXEMPT", ()) or ()),
        require_approval=frozenset(guard.get("REQUIRE_APPROVAL", ()) or ()),
    )


__all__ = ["build_ag_ui_config"]
