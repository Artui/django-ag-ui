from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django_pydantic_agent.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_pydantic_agent.policy.guard.types.tool_guard_config import ToolGuardConfig

from django_ag_ui.config.types.run_context_config import RunContextConfig


@dataclass(frozen=True)
class AGUIConfig:
    """An endpoint's resolved scalar configuration.

    Every field is **already resolved** — there is no "unset" state and no
    settings lookup left to do. [`AGUIServer`][django_ag_ui.AGUIServer] builds
    one in ``__init__``
    (via [`build_ag_ui_config`][django_ag_ui.build_ag_ui_config],
    which reads ``DJANGO_AG_UI``) and threads it to the agent view and every
    sub-view.

    Resolving once is what lets two endpoints in one project differ: read at
    request time these values could only ever be global, forcing an
    ``/internal/agent`` and a ``/public/agent`` to share one tool-guard policy,
    one retry budget, one upload cap. Collaborators are not here at all — they
    are constructor arguments taking real objects.

    Do **not** construct this directly to override a field: a
    partially-specified config would silently discard the project's own
    ``DJANGO_AG_UI`` values. Use ``build_ag_ui_config(retries=3)``, which layers
    overrides over the settings.
    """

    model: Any
    """The Pydantic-AI model: a ``"provider:name"`` string (e.g.
    ``"anthropic:claude-sonnet-4.6"``) or a pre-built ``Model`` instance. May be
    ``None`` here; the agent factory raises a clear error if it is still unset
    when an agent is actually built."""

    api_key: str | None
    """API key handed to the provider when ``model`` is a ``"provider:name"``
    string, so the key comes from settings rather than the environment. Ignored
    when a ``provider`` is passed or ``model`` is already a ``Model``."""

    system_prompt: str | None
    """Override for the agent's default system prompt. ``None`` uses
    [`DEFAULT_SYSTEM_PROMPT`][django_ag_ui.DEFAULT_SYSTEM_PROMPT]."""

    model_settings: dict[str, Any] | None
    """Pydantic-AI ``ModelSettings`` (e.g. ``{"temperature": 0.2}``) passed
    straight to the ``Agent``. ``None`` leaves the model defaults untouched."""

    retries: int | None
    """Default tool/output retry budget passed to the ``Agent``. ``None`` uses
    Pydantic-AI's default."""

    attachment_max_bytes: int
    """Maximum accepted upload size in bytes (server-authoritative). ``0``
    disables the cap."""

    attachment_allowed_types: tuple[str, ...]
    """Allowed (client-declared) content types for uploads. Empty accepts any."""

    manage_system_prompt: str
    """Who owns the system prompt on the wire: ``"server"`` (the agent's prompt
    is authoritative and a client-posted system message is ignored) or
    ``"client"``. ``instructions`` are always server-side regardless."""

    allow_uploaded_files: bool
    """Whether ``UploadedFile`` references in client-submitted messages are
    honoured. ``False`` drops them with a warning before they reach the agent."""

    forward_reasoning: bool
    """Whether to forward a reasoning model's chain-of-thought to the client as
    AG-UI reasoning events — a pure adapter pass-through, only emitted if a
    thinking budget is enabled via ``model_settings``."""

    transcription_max_bytes: int
    """Maximum accepted audio-clip size in bytes (server-authoritative). ``0``
    disables the cap."""

    transcription_allowed_types: tuple[str, ...]
    """Allowed (client-declared) content types for voice clips. Empty accepts
    any."""

    thread_list_limit: int
    """Maximum threads the index endpoint returns in one call. A larger
    ``?limit`` is clamped to this ceiling."""

    tool_guard: ToolGuardConfig
    """Server-side destructive-tool approval policy. When enabled, a
    ``ToolGuard`` capability flips destructive tools to require the AG-UI
    approval interrupt."""

    approval_prompts: Mapping[str, str]
    """Human-readable questions for gated tools, by tool name, stamped onto the
    approval interrupt as ``x-confirm``.

    The question a client would otherwise ask is the call spelled out, which is
    accurate and unreadable. A registry tool's own ``@tool(confirm=...)`` is
    folded in here by ``AGUIServer``, so this only needs entries for tools whose
    schema carries none — a spec tool reaching the agent in-process, or a bridged
    MCP tool. A tool with no entry keeps the generated question."""

    tool_failure: ToolFailureConfig
    """What an unhandled tool exception costs. On by default, so a raising tool
    fails its own call and the turn carries on rather than ending in
    ``RUN_ERROR`` with the answer so far discarded."""

    run_context: RunContextConfig
    """What client-supplied context reaches the model: the host page's own
    ``RunAgentInput.context`` entries and the attachment refs riding the posted
    messages, fenced and labelled as data, capped by a character ceiling."""


__all__ = ["AGUIConfig"]
