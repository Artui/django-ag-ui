from __future__ import annotations

from typing import Any

from ag_ui.core import Message
from django.http import HttpRequest
from pydantic import TypeAdapter

# A reusable, immutable adapter for (de)serialising the AG-UI message union.
_MESSAGES = TypeAdapter(list[Message])


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


__all__ = ["messages_from_jsonable", "messages_to_jsonable", "owner_id_for"]
