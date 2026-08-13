from __future__ import annotations

from typing import Any

from ag_ui.core import Message
from pydantic import TypeAdapter

# In the transport, not the substrate: a ``Conversation``'s messages are JSON
# records whose shape the transport owns, so the AG-UI wire type is converted at
# the edge that speaks it and the store round-trips the result verbatim.
_MESSAGES = TypeAdapter(list[Message])


def messages_to_jsonable(messages: list[Message]) -> list[dict[str, Any]]:
    """Serialise AG-UI messages to JSON-safe dicts for storage.

    ``by_alias=True`` is load-bearing, not a style choice. The ``ag_ui.core``
    models declare ``alias_generator=to_camel``, so the alias *is* the AG-UI wire
    format: dumping by field name emits ``tool_calls`` / ``tool_call_id``, which
    a client reading the protocol's ``toolCalls`` / ``toolCallId`` sees as
    absent, so an assistant turn's tool calls and every tool result vanish from a
    restored transcript while the prose survives. Nothing in Python notices,
    because ``populate_by_name=True`` makes the round-trip symmetric — the
    mismatch exists only where another language reads the JSON.

    ``exclude_none=True`` is the same class of fix, found the same way. The
    protocol's Python models type an assistant turn's ``tool_calls`` as
    ``list[ToolCall] | None``, so a turn that called no tool serialises as
    ``"toolCalls": null`` — while the protocol's **TypeScript** schema types that
    field optional-and-not-nullable and *rejects* the null outright
    (``Expected array, received null``). Two SDKs of one protocol disagree about
    the wire, and this end is the one that can stop emitting it: an absent field
    is valid in both, so dropping nulls is conformance rather than tidiness. It
    cost a released version of the web component, whose history replay threw on
    the null and dropped every later turn from a restored transcript.

    Omitting a null loses nothing on the way back: ``messages_from_jsonable``
    validates against the same models, where every nullable field is also
    optional and defaults to ``None``.
    """
    return _MESSAGES.dump_python(messages, mode="json", by_alias=True, exclude_none=True)


def messages_from_jsonable(raw: Any) -> list[Message]:
    """Rebuild AG-UI messages from stored JSON-safe dicts.

    Accepts both spellings of every field (``populate_by_name=True`` on the
    ``ag_ui.core`` models), which is what lets records written before the
    ``by_alias`` fix above still load.
    """
    return _MESSAGES.validate_python(raw)


def stored_messages_to_wire(raw: Any) -> list[dict[str, Any]]:
    """Re-serialise stored message records into the AG-UI wire shape.

    A row written before ``messages_to_jsonable`` dumped by alias holds the
    Python field spelling. Validating and re-dumping normalises both eras on
    read, so no data migration is needed.
    """
    return messages_to_jsonable(messages_from_jsonable(raw))


__all__ = ["messages_from_jsonable", "messages_to_jsonable", "stored_messages_to_wire"]
