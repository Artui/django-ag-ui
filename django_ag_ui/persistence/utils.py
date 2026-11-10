from __future__ import annotations

from typing import Any

from ag_ui.core import Message
from pydantic import TypeAdapter

# In the transport, not the substrate: a ``Conversation``'s messages are JSON
# records whose shape the transport owns, so the AG-UI wire type is converted at
# the edge that speaks it and the store round-trips the result verbatim.
_MESSAGES = TypeAdapter(list[Message])


def messages_to_jsonable(messages: list[Message]) -> list[dict[str, Any]]:
    """Serialise AG-UI messages to JSON-safe dicts for storage."""
    return _MESSAGES.dump_python(messages, mode="json", by_alias=True, exclude_none=True)


def messages_from_jsonable(raw: Any) -> list[Message]:
    """Rebuild AG-UI messages from stored JSON-safe dicts."""
    return _MESSAGES.validate_python(raw)


def stored_messages_to_wire(raw: Any) -> list[dict[str, Any]]:
    """Re-serialise stored message records into the AG-UI wire shape."""
    return messages_to_jsonable(messages_from_jsonable(raw))


__all__ = ["messages_from_jsonable", "messages_to_jsonable", "stored_messages_to_wire"]
