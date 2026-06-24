from __future__ import annotations

from typing import Any

from ag_ui.core import Message
from django.http import HttpRequest
from pydantic import TypeAdapter

# A reusable, immutable adapter for (de)serialising the AG-UI message union.
_MESSAGES = TypeAdapter(list[Message])

_TITLE_LIMIT = 60
_PREVIEW_LIMIT = 120
_DEFAULT_TITLE = "New conversation"


def messages_to_jsonable(messages: list[Message]) -> list[dict[str, Any]]:
    """Serialise AG-UI messages to JSON-safe dicts for storage."""
    return _MESSAGES.dump_python(messages, mode="json")


def messages_from_jsonable(raw: Any) -> list[Message]:
    """Rebuild AG-UI messages from stored JSON-safe dicts."""
    return _MESSAGES.validate_python(raw)


def owner_id_for(request: HttpRequest) -> str | None:
    """The authenticated user's id as a string, or ``None`` when anonymous."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(user.pk)
    return None


def derive_title(messages: list[Message]) -> str:
    """A thread title: the first user message, collapsed + truncated.

    Falls back to a generic label when there is no user text yet (a brand-new
    or assistant-only thread). Stores that record an explicit rename use that
    instead of calling this.
    """
    for message in messages:
        if getattr(message, "role", None) == "user":
            text = _clean(getattr(message, "content", None))
            if text:
                return _truncate(text, _TITLE_LIMIT)
    return _DEFAULT_TITLE


def derive_preview(messages: list[Message]) -> str:
    """A one-line preview: the latest message with text, collapsed + truncated."""
    for message in reversed(messages):
        text = _clean(getattr(message, "content", None))
        if text:
            return _truncate(text, _PREVIEW_LIMIT)
    return ""


def _clean(content: Any) -> str:
    """Whitespace-collapsed message text, or ``""`` for non-string content."""
    if not isinstance(content, str):
        return ""
    return " ".join(content.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = [
    "derive_preview",
    "derive_title",
    "messages_from_jsonable",
    "messages_to_jsonable",
    "owner_id_for",
]
