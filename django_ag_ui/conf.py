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

    model: str | None
    """The Pydantic-AI model string (e.g. ``"anthropic:claude-sonnet-4.6"``).
    Optional here; the agent factory raises a clear error if it is unset
    when an agent is actually built."""

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

    drf_mcp_server: str | None
    """Dotted path to a ``drf-mcp-server`` ``MCPServer`` instance whose tools
    are exposed to the agent in-process (requires the ``[drf-mcp]`` extra).
    ``None`` disables the bridge."""


def get_settings() -> AppSettings:
    """Read the active ``DJANGO_AG_UI`` settings dict into an ``AppSettings``."""
    raw: dict[str, Any] = getattr(settings, _SETTING_NAME, {}) or {}
    return AppSettings(
        model=raw.get("MODEL"),
        auto_confirm=bool(raw.get("AUTO_CONFIRM", False)),
        audit_logger=raw.get("AUDIT_LOGGER"),
        system_prompt=raw.get("SYSTEM_PROMPT"),
        model_settings=raw.get("MODEL_SETTINGS"),
        retries=raw.get("RETRIES"),
        agent_factory=raw.get("AGENT_FACTORY"),
        toolsets=tuple(raw.get("TOOLSETS", ()) or ()),
        capabilities=tuple(raw.get("CAPABILITIES", ()) or ()),
        conversation_store=raw.get("CONVERSATION_STORE"),
        drf_mcp_server=raw.get("DRF_MCP_SERVER"),
    )


__all__ = ["AppSettings", "get_settings"]
