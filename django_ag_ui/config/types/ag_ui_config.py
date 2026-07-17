from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_ag_ui.policy.guard.types.tool_guard_config import ToolGuardConfig


@dataclass(frozen=True)
class AGUIConfig:
    """An endpoint's resolved scalar configuration.

    Every field is **already resolved** — there is no "unset" state and no
    settings lookup left to do. :class:`AGUIServer` builds one in ``__init__``
    (via :func:`~django_ag_ui.config.build_ag_ui_config.build_ag_ui_config`,
    which reads ``DJANGO_AG_UI``) and threads it to the agent view and every
    sub-view.

    That indirection is the point. Read at request time, these values could only
    ever be global, so two AG-UI endpoints in one project could not differ on any
    of them — an ``/internal/agent`` and a ``/public/agent`` were forced to share
    one tool-guard policy, one retry budget, one upload cap.

    Replaces the old ``AppSettings``, which mixed these scalars with *dotted
    paths to collaborators*. Those are now constructor arguments taking real
    objects (``toolsets=``, ``capabilities=``, ``conversation_store=``, …), so
    what remains here is only values.

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
    :data:`DEFAULT_SYSTEM_PROMPT`."""

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


__all__ = ["AGUIConfig"]
