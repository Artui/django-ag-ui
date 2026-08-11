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
    """Serialise AG-UI messages to JSON-safe dicts for storage.

    ``by_alias=True`` is load-bearing, not a style choice. The ``ag_ui.core``
    models are declared with ``alias_generator=to_camel``, so the alias *is*
    the AG-UI wire format and the Python field name is an implementation
    detail. Dumping by field name emits ``tool_calls`` / ``tool_call_id``,
    which a client reading the protocol's ``toolCalls`` / ``toolCallId`` sees
    as absent — so an assistant turn's tool calls and every tool result
    silently vanish from a restored transcript while the prose survives.

    Nothing in Python notices, because ``populate_by_name=True`` means
    :func:`messages_from_jsonable` accepts either spelling: the round-trip is
    symmetric, and therefore passes while being wrong. The mismatch exists
    only at the boundary where another language reads the JSON.
    """
    return _MESSAGES.dump_python(messages, mode="json", by_alias=True)


def messages_from_jsonable(raw: Any) -> list[Message]:
    """Rebuild AG-UI messages from stored JSON-safe dicts.

    Accepts both spellings of every field (``populate_by_name=True`` on the
    ``ag_ui.core`` models), which is what lets records written before the
    ``by_alias`` fix above still load.
    """
    return _MESSAGES.validate_python(raw)


def stored_messages_to_wire(raw: Any) -> list[dict[str, Any]]:
    """Re-serialise stored message records into the AG-UI wire shape.

    Storage records are not automatically wire records: anything written
    before ``messages_to_jsonable`` started dumping by alias is held in the
    Python field spelling, and handing those to a client is exactly the defect
    that fix closes. Validating and re-dumping normalises both eras on read,
    so no data migration is needed and an old thread renders like a new one.
    """
    return messages_to_jsonable(messages_from_jsonable(raw))


__all__ = ["messages_from_jsonable", "messages_to_jsonable", "stored_messages_to_wire"]
