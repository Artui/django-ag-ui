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

# Names this package never read, but that a reader could reasonably expect it to
# — the ones worth answering by name rather than as "unknown key". A project
# setting one of these got the default and no indication otherwise.
_NEVER_READ_SETTINGS: dict[str, str] = {
    "ALLOW_ANONYMOUS": (
        "never a setting of this package — pass allow_anonymous=True to the "
        "store you construct, e.g. DefaultStepStore(request, allow_anonymous=True)"
    ),
}

# Every key ``build_ag_ui_config`` reads. The whole list, because anything not on
# it is rejected below; a test reads the builder's own source and fails if the
# two drift.
_KNOWN_SETTINGS: frozenset[str] = frozenset(
    {
        "ALLOW_UPLOADED_FILES",
        "API_KEY",
        "APPROVAL_PROMPTS",
        "ATTACHMENT_ALLOWED_TYPES",
        "ATTACHMENT_MAX_BYTES",
        "FORWARD_REASONING",
        "MANAGE_SYSTEM_PROMPT",
        "MODEL",
        "MODEL_SETTINGS",
        "RETRIES",
        "RUN_CONTEXT",
        "RUN_LIST_LIMIT",
        "SYSTEM_PROMPT",
        "THREAD_LIST_LIMIT",
        "TOOL_FAILURE",
        "TOOL_GUARD",
        "TRANSCRIPTION_ALLOWED_TYPES",
        "TRANSCRIPTION_MAX_BYTES",
    }
)

_UNKNOWN_ADVICE = "not a django-ag-ui setting — check the spelling against the settings table"


def check_removed_settings() -> None:
    """Reject any ``DJANGO_AG_UI`` key this package does not read.

    Called from ``AGUIServer.__init__``, so a stale settings dict fails when
    the URL conf is imported rather than on some later request. Left in place, an
    unread key is **silently dropped**: an agent quietly loses its ``TOOLSETS``,
    or runs without the ``TOOL_GUARD`` approval policy the project believes it
    configured. A warning would scroll past in a deploy log.

    Three kinds of key are refused, each with its own answer:

    - the collaborators **removed in 0.19.0**, which named a dotted path and are
      constructor arguments now;
    - names this package **never read** but that a reader could reasonably expect
      it to — ``ALLOW_ANONYMOUS`` is the one that has actually cost somebody an
      afternoon, since it looks like a switch and is a store argument;
    - anything else, which is a typo or a setting meant for another package.

    The last case is what makes the first two exhaustive rather than a list
    somebody has to remember to extend. A key added to
    [`build_ag_ui_config`][django_ag_ui.build_ag_ui_config] must be added to
    ``_KNOWN_SETTINGS`` with it, and a test reads the builder's own source to
    make sure it was.
    """
    user_settings: dict[str, object] = getattr(django_settings, "DJANGO_AG_UI", {}) or {}
    rejected: list[tuple[str, str]] = sorted(
        (name, _advice(name)) for name in user_settings if name not in _KNOWN_SETTINGS
    )
    if not rejected:
        return
    details: str = "\n".join(f"  DJANGO_AG_UI[{name!r}] — {advice}" for name, advice in rejected)
    raise ImproperlyConfigured(
        "These DJANGO_AG_UI keys are not read by django-ag-ui. They would be "
        "silently ignored if left in place, so they are rejected:\n"
        f"{details}"
    )


def _advice(name: str) -> str:
    """What to do about one unread key, in the most specific terms available."""
    if name in _REMOVED_SETTINGS:
        return f"removed in 0.19.0, {_REMOVED_SETTINGS[name]}"
    if name in _NEVER_READ_SETTINGS:
        return _NEVER_READ_SETTINGS[name]
    return _UNKNOWN_ADVICE


__all__ = ["check_removed_settings"]
