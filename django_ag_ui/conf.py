from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings

_SETTING_NAME = "DJANGO_AG_UI"


@dataclass(frozen=True)
class AppSettings:
    """Snapshot of the user-configurable ``DJANGO_AG_UI`` settings.

    Built fresh on every read so test overrides take effect immediately
    (Django's ``settings`` object is the cache, not this dataclass).
    """

    model: Any
    """The Pydantic-AI model: a ``"provider:name"`` string (e.g.
    ``"anthropic:claude-sonnet-4.6"``) or a pre-built ``Model`` instance.
    Optional here; the agent factory raises a clear error if it is unset
    when an agent is actually built."""

    api_key: str | None
    """API key handed to the provider when ``MODEL`` is a ``"provider:name"``
    string, so the key comes from settings rather than the environment.
    Ignored when ``PROVIDER`` is set or ``MODEL`` is already a ``Model``."""

    provider: Any
    """Optional Pydantic-AI ``Provider`` instance (or dotted path to one) used
    to build the model — for a custom ``base_url`` / client. When set, ``MODEL``
    is just the model name's ``"provider:name"`` string and ``API_KEY`` is
    ignored (the provider carries its own credentials)."""

    auto_confirm: bool
    """When ``True``, destructive tools do not require client-side
    confirmation. Surfaced to the frontend so it can skip the modal."""

    audit_logger: str | None
    """Dotted path to an ``AuditLogger`` implementation. ``None`` means
    use the package default (a no-op logger)."""

    system_prompt: str | None
    """Optional override for the agent's system prompt."""

    model_settings: dict[str, Any] | None
    """Pydantic-AI ``ModelSettings`` (e.g. ``{"temperature": 0.2,
    "max_tokens": 1024}``) passed straight to the ``Agent``. ``None`` leaves
    the model defaults untouched."""

    retries: int | None
    """Default tool/output retry budget passed to the ``Agent``. ``None`` uses
    Pydantic-AI's default."""

    agent_factory: str | None
    """Dotted path to a callable ``(registry, settings) -> Agent`` that fully
    replaces the built-in factory — the escape hatch for arbitrary Pydantic-AI
    configuration. ``None`` uses the built-in
    :func:`~django_ag_ui.agent.agent_factory.build_agent`."""

    toolsets: tuple[str, ...]
    """Dotted paths to extra Pydantic-AI toolsets (or zero-arg callables
    returning one) merged into the agent's catalog — e.g. an MCP-client
    toolset or the ``drf-mcp`` bridge. Empty by default."""

    capabilities: tuple[str, ...]
    """Dotted paths to Pydantic-AI capabilities (or zero-arg callables
    returning one) passed to the ``Agent``. Empty by default."""

    conversation_store: str | None
    """Dotted path to a ``ConversationStore`` for server-side persistence.
    ``None`` keeps the server stateless (the default ``NullConversationStore``)."""

    attachment_store: str | None
    """Dotted path to an ``AttachmentStore`` for file uploads. ``None`` keeps
    uploads disabled (the default ``NullAttachmentStore``)."""

    attachment_max_bytes: int
    """Maximum accepted upload size in bytes (server-authoritative). ``0``
    disables the cap. Default 10 MiB."""

    attachment_allowed_types: tuple[str, ...]
    """Allowed (client-declared) content types for uploads. Empty accepts any
    type; otherwise an upload whose ``Content-Type`` is not listed is rejected."""

    forward_reasoning: bool
    """When ``True`` (default), forward a reasoning model's chain-of-thought to
    the client as AG-UI reasoning events (a pure adapter pass-through — only
    emitted if a thinking budget is enabled via ``MODEL_SETTINGS``). Set ``False``
    to let the model reason privately while stripping those events from the
    stream."""

    transcription_backend: str | None
    """Dotted path to a ``TranscriptionBackend`` for voice input. ``None`` keeps
    voice disabled (the default ``NullTranscriptionBackend``)."""

    transcription_max_bytes: int
    """Maximum accepted audio-clip size in bytes (server-authoritative). ``0``
    disables the cap. Default 25 MiB (the OpenAI transcription limit)."""

    transcription_allowed_types: tuple[str, ...]
    """Allowed (client-declared) content types for voice clips. Empty accepts any
    type; otherwise a clip whose ``Content-Type`` is not listed is rejected."""

    drf_mcp_server: str | None
    """Dotted path to a ``drf-mcp-server`` ``MCPServer`` instance whose tools
    are exposed to the agent in-process (requires the ``[drf-mcp]`` extra).
    ``None`` disables the bridge."""


def get_settings() -> AppSettings:
    """Read the active ``DJANGO_AG_UI`` settings dict into an ``AppSettings``."""
    raw: dict[str, Any] = getattr(settings, _SETTING_NAME, {}) or {}
    return AppSettings(
        model=raw.get("MODEL"),
        api_key=raw.get("API_KEY"),
        provider=raw.get("PROVIDER"),
        auto_confirm=bool(raw.get("AUTO_CONFIRM", False)),
        audit_logger=raw.get("AUDIT_LOGGER"),
        system_prompt=raw.get("SYSTEM_PROMPT"),
        model_settings=raw.get("MODEL_SETTINGS"),
        retries=raw.get("RETRIES"),
        agent_factory=raw.get("AGENT_FACTORY"),
        toolsets=tuple(raw.get("TOOLSETS", ()) or ()),
        capabilities=tuple(raw.get("CAPABILITIES", ()) or ()),
        conversation_store=raw.get("CONVERSATION_STORE"),
        attachment_store=raw.get("ATTACHMENT_STORE"),
        attachment_max_bytes=int(raw.get("ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024)),
        attachment_allowed_types=tuple(raw.get("ATTACHMENT_ALLOWED_TYPES", ()) or ()),
        forward_reasoning=bool(raw.get("FORWARD_REASONING", True)),
        transcription_backend=raw.get("TRANSCRIPTION_BACKEND"),
        transcription_max_bytes=int(raw.get("TRANSCRIPTION_MAX_BYTES", 25 * 1024 * 1024)),
        transcription_allowed_types=tuple(raw.get("TRANSCRIPTION_ALLOWED_TYPES", ()) or ()),
        drf_mcp_server=raw.get("DRF_MCP_SERVER"),
    )


__all__ = ["AppSettings", "get_settings"]
