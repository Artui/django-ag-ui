from __future__ import annotations

from typing import Any

from ag_ui.core import Message
from pydantic import TypeAdapter

# A reusable, immutable adapter for (de)serialising the AG-UI message union.
#
# This lives in the transport, not the substrate: a ``Conversation``'s messages
# are JSON records whose shape the transport owns, so the AG-UI wire type is
# converted here — at the edge that speaks it — and the store round-trips the
# result verbatim (client message ids included).
_MESSAGES = TypeAdapter(list[Message])


def messages_to_jsonable(messages: list[Message]) -> list[dict[str, Any]]:
    """Serialise AG-UI messages to JSON-safe dicts for storage."""
    return _MESSAGES.dump_python(messages, mode="json")


def messages_from_jsonable(raw: Any) -> list[Message]:
    """Rebuild AG-UI messages from stored JSON-safe dicts."""
    return _MESSAGES.validate_python(raw)


__all__ = ["messages_from_jsonable", "messages_to_jsonable"]
