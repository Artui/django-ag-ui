from __future__ import annotations

from ag_ui.core import AssistantMessage, FunctionCall, ToolCall, ToolMessage, UserMessage

from django_ag_ui.persistence.utils import (
    messages_from_jsonable,
    messages_to_jsonable,
    stored_messages_to_wire,
)


def test_messages_round_trip() -> None:
    """The AG-UI wire shape survives a store round trip, ids included.

    The substrate persists transport-owned records verbatim, so this codec is
    the only place the AG-UI ``Message`` union is converted — encode on the way
    into a store, decode on the way back out.
    """
    messages = [
        UserMessage(id="u1", role="user", content="hi"),
        AssistantMessage(id="a1", role="assistant", content="hello"),
    ]

    raw = messages_to_jsonable(messages)
    assert raw[0]["id"] == "u1"
    assert isinstance(raw[0], dict)

    back = messages_from_jsonable(raw)
    assert [m.id for m in back] == ["u1", "a1"]
    assert back[0].role == "user"
    assert back[1].content == "hello"


def test_tool_exchange_is_serialised_under_the_protocol_key_names() -> None:
    """Keys, not values — the round trip above cannot see this.

    ``populate_by_name`` makes decoding accept either spelling, so encode then
    decode agrees with itself whichever one is written. The only consumer that
    can tell is one reading the JSON in another language, and AG-UI names these
    fields ``toolCalls`` and ``toolCallId``. Asserting the emitted keys is the
    whole point of this test.
    """
    messages = [
        AssistantMessage(
            id="a1",
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    type="function",
                    function=FunctionCall(name="read_page", arguments="{}"),
                )
            ],
        ),
        ToolMessage(id="t1", role="tool", content="{}", tool_call_id="call_1"),
    ]

    raw = messages_to_jsonable(messages)

    assert "toolCalls" in raw[0]
    assert "tool_calls" not in raw[0]
    assert raw[0]["toolCalls"][0]["function"]["name"] == "read_page"
    assert raw[1]["toolCallId"] == "call_1"
    assert "tool_call_id" not in raw[1]


def test_stored_records_in_the_old_spelling_come_back_on_the_wire_shape() -> None:
    """Rows written before the fix must still render for a client.

    Re-serialising on read is what covers them, so a thread stored in the
    Python field spelling needs no data migration to come back usable.
    """
    stored = [
        {
            "id": "a1",
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_page", "arguments": "{}"},
                }
            ],
        },
        {"id": "t1", "role": "tool", "content": "{}", "tool_call_id": "call_1"},
    ]

    wire = stored_messages_to_wire(stored)

    assert wire[0]["toolCalls"][0]["id"] == "call_1"
    assert wire[1]["toolCallId"] == "call_1"
