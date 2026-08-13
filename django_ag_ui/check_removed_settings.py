from __future__ import annotations

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

# Setting name → how to say the same thing now. Each named a collaborator by
# dotted path; collaborators are now passed as objects from ``urls.py``.
_REMOVED_SETTINGS: dict[str, str] = {
    "AGENT_FACTORY": "pass agent_factory=your_factory to AGUIServer(...)",
    "TOOLSETS": "pass toolsets=[YourToolset()] to AGUIServer(...)",
    "CAPABILITIES": "pass capabilities=[YourCapability()] to AGUIServer(...)",
    "AUDIT_LOGGER": "pass audit_logger=YourLogger() to AGUIServer(...)",
    "CONVERSATION_STORE": "pass conversation_store=YourStore() to AGUIServer(...)",
    "ATTACHMENT_STORE": "pass attachment_store=YourStore() to AGUIServer(...)",
    "TRANSCRIPTION_BACKEND": "pass transcription_backend=YourBackend() to AGUIServer(...)",
    "DRF_MCP_SERVER": "pass drf_mcp_server=your_mcp_server to AGUIServer(...)",
    "SERVICE_SPECS": "pass service_specs={...} to AGUIServer(...)",
    "PROVIDER": "pass provider=YourProvider() to AGUIServer(...)",
}


def check_removed_settings() -> None:
    """Reject removed ``DJANGO_AG_UI`` keys instead of ignoring them.

    Called from ``AGUIServer.__init__``, so a stale settings dict fails when
    the URL conf is imported rather than on some later request. Left in place, a
    removed key is **silently dropped**: an agent quietly loses its ``TOOLSETS``,
    or runs without the ``TOOL_GUARD`` approval policy the project believes it
    configured. A warning would scroll past in a deploy log.
    """
    user_settings: dict[str, object] = getattr(django_settings, "DJANGO_AG_UI", {}) or {}
    present: list[str] = [name for name in _REMOVED_SETTINGS if name in user_settings]
    if not present:
        return
    details: str = "\n".join(
        f"  DJANGO_AG_UI[{name!r}] — {_REMOVED_SETTINGS[name]}" for name in present
    )
    raise ImproperlyConfigured(
        "These DJANGO_AG_UI settings were removed in 0.19.0; the collaborators "
        "they named are now constructor arguments. They would be silently "
        "ignored if left in place, so they are rejected:\n"
        f"{details}"
    )


__all__ = ["check_removed_settings"]
