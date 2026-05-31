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


def get_settings() -> AppSettings:
    """Read the active ``DJANGO_AG_UI`` settings dict into an ``AppSettings``."""
    raw: dict[str, Any] = getattr(settings, _SETTING_NAME, {}) or {}
    return AppSettings(
        model=raw.get("MODEL"),
        auto_confirm=bool(raw.get("AUTO_CONFIRM", False)),
        audit_logger=raw.get("AUDIT_LOGGER"),
        system_prompt=raw.get("SYSTEM_PROMPT"),
    )


__all__ = ["AppSettings", "get_settings"]
