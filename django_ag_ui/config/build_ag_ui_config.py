from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast, get_args

from django.core.exceptions import ImproperlyConfigured
from django_pydantic_agent import AttachmentInlineConfig
from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig

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
    approval_prompts: Mapping[str, str] | None = None,
    tool_guard: ToolGuardConfig | None = None,
    tool_failure: ToolFailureConfig | None = None,
    run_context: RunContextConfig | None = None,
) -> AGUIConfig:
    """Resolve an [`AGUIConfig`][django_ag_ui.AGUIConfig] from ``DJANGO_AG_UI``,
    applying overrides.

    The single place the scalar settings are read.
    [`AGUIServer`][django_ag_ui.AGUIServer] calls this
    once in ``__init__``; nothing reads these settings per request, which is what
    lets two endpoints in one project hold different values.

    Every argument is ``None`` by default, meaning "take it from settings". Pass
    one to override just that field for this endpoint:

        AGUIServer(registry, config=build_ag_ui_config(retries=3))

    Use this rather than constructing [`AGUIConfig`][django_ag_ui.AGUIConfig]
    directly — it is what
    layers your overrides *over* the project's settings instead of discarding
    them.
    """

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
        # Not read from settings: it is a dataclass rather than a scalar, and the
        # ecosystem's rule is that collaborators arrive as objects rather than
        # dotted paths. Pass one to ``build_ag_ui_config`` to change it.
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
    """Build a ``ToolGuardConfig`` from the ``TOOL_GUARD`` settings dict.

    Absent / falsy → a disabled config (the default). Names are normalised to
    ``frozenset`` so lookups in the capability are O(1) and order-insensitive.
    """
    guard: dict[str, Any] = raw or {}
    return ToolGuardConfig(
        enabled=bool(guard.get("ENABLED", False)),
        exempt=frozenset(guard.get("EXEMPT", ()) or ()),
        require_approval=frozenset(guard.get("REQUIRE_APPROVAL", ()) or ()),
    )


def _parse_tool_failure(raw: Any) -> ToolFailureConfig:
    """Build a ``ToolFailureConfig`` from the ``TOOL_FAILURE`` settings dict.

    Absent → the record's own defaults, which turn the policy **on**, unlike
    ``TOOL_GUARD`` above where absent means no gate. Reading ``ENABLED`` with a
    ``True`` default is what keeps "no settings at all" and "an empty dict" the
    same answer.
    """
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
    """Build a [`RunContextConfig`][django_ag_ui.RunContextConfig] from the
    ``RUN_CONTEXT`` settings dict.

    Absent or empty → both sources on and the default ceiling, following
    ``TOOL_FAILURE`` rather than ``TOOL_GUARD``. A project that wants nothing a
    client puts in ``context`` reaching the model says ``CLIENT_CONTEXT: False``.

    ``MAX_CHARS`` exists because ``context`` is unbounded client-supplied text,
    limited only by ``DATA_UPLOAD_MAX_MEMORY_SIZE``, and it reaches the model on
    every request of every run. 20 000 characters is roughly 5 000 tokens: a
    ceiling on a pathological page, not a budget to plan against.

    ``DELIVERY`` picks the channel and defaults to ``"instructions"``, which is
    what every release before it did. An unrecognised value **raises** rather
    than falling back: the two channels have different security properties, so
    a typo silently resolving to the more permissive one is the one outcome
    worth refusing at startup.
    """
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
