from __future__ import annotations

import warnings
from typing import Any


def warn_if_csrf_unstated(csrf_exempt: bool | None, get_user: Any) -> None:
    """Warn when nothing in the configuration says how requests authenticate."""
    if csrf_exempt is not None or get_user is not None:
        return
    warnings.warn(
        "django-ag-ui: this AG-UI endpoint is CSRF-exempt (the default) and has "
        "no get_user hook, so the acting user can only be coming from Django's "
        "session cookie — and tools act as that user, which lets any "
        "third-party page drive the agent as whoever is logged in. Pass "
        "csrf_exempt=False (and send the CSRF token from the client) for "
        "cookie authentication, or csrf_exempt=True to confirm your clients "
        "authenticate by header (Bearer / API key), where CSRF does not apply.",
        RuntimeWarning,
        stacklevel=3,
    )


__all__ = ["warn_if_csrf_unstated"]
