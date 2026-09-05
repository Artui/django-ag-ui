from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django_pydantic_agent import AttachmentInlineConfig
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

    api_key: str | None = field(repr=False)
    """API key handed to the provider when ``model`` is a ``"provider:name"``
    string, so the key comes from settings rather than the environment. Ignored
    when a ``provider`` is passed or ``model`` is already a ``Model``.

    ``repr=False`` because this record is bound to a plainly-named local on every
    path that builds an agent, so the generated ``repr`` would print the provider
    secret into the frame locals of a technical-500 page or an error-reporting
    event. Name-based scrubbing does not catch it there: the key is nested inside
    another object's ``repr`` rather than sitting in a field called ``api_key``.
    Read the attribute to use it -- only the rendering is suppressed."""

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

    attachment_inline: AttachmentInlineConfig | None
    """How much of an attachment ``read_attachment`` hands the model, or ``None``
    for the substrate's defaults.

    A separate budget from the two above, and deliberately a smaller one: those
    bound what may be *stored*, this bounds what rides in every model request for
    the rest of the run. But the two must be set together, because a file above
    this limit and below ``attachment_max_bytes`` uploads, shows a chip, and can
    never be read -- indistinguishable from success on screen. Raising the upload
    cap without raising this one widens that band."""

    manage_system_prompt: str
    """Who owns the system prompt on the wire: ``"server"`` (the agent's prompt
    is authoritative and a client-posted system message is ignored) or
    ``"client"``. ``instructions`` are always server-side regardless."""

    allow_uploaded_files: bool
    """Whether ``UploadedFile`` references in client-submitted messages are
    honoured. ``False`` drops them with a warning before they reach the agent."""

    forward_reasoning: bool
    """Whether to forward a reasoning model's chain-of-thought to the client as
    AG-UI reasoning events — a pure adapter pass-through.

    Whether there is anything *to* forward is the model's business rather than
    this package's, and it is emphatically not always an opt-in. Pydantic-AI's
    OpenAI-compatible chat path builds a ``ThinkingPart`` out of whatever the
    provider returned in ``reasoning`` / ``reasoning_content``, consulting no
    setting at all, and its DeepSeek profile marks ``deepseek-reasoner`` with
    ``thinking_always_enabled`` because that model cannot be told to stop. On
    such a provider the default here — ``True`` — streams chain-of-thought to
    every browser with nothing configured and nothing asked for. Set ``False``
    to keep it server-side."""

    transcription_max_bytes: int
    """Maximum accepted audio-clip size in bytes (server-authoritative). ``0``
    disables the cap."""

    transcription_allowed_types: tuple[str, ...]
    """Allowed (client-declared) content types for voice clips. Empty accepts
    any."""

    thread_list_limit: int
    """Maximum threads the index endpoint returns in one call. A larger
    ``?limit`` is clamped to this ceiling."""

    run_list_limit: int
    """Maximum runs the run index returns in one call, newest first.

    A much tighter ceiling than ``thread_list_limit`` because the rows cost far
    more: the thread index answers from metadata alone, while every run row loads
    that run's last snapshot and holds its whole message list resident while the
    response is built. ``0`` disables the cap and restores the unbounded
    behaviour."""

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
