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


def test_a_turn_that_called_no_tool_omits_tool_calls_rather_than_nulling_it() -> None:
    """The two SDKs of this protocol disagree about the wire, and this end yields.

    ``ag_ui.core`` types the field ``list[ToolCall] | None``, so an assistant turn
    that called nothing serialised as ``"toolCalls": null``. The protocol's
    TypeScript schema types it optional-and-not-nullable and rejects that null
    outright, which cost a released version of the web component: its history
    replay threw on the null and dropped every later turn from the transcript.

    An absent field is valid in both, so the null simply stops being emitted.
    """
    raw = messages_to_jsonable([AssistantMessage(id="a1", role="assistant", content="hello")])

    assert "toolCalls" not in raw[0]
    assert None not in raw[0].values()


def test_dropping_nulls_loses_nothing_on_the_way_back() -> None:
    """Every nullable field on these models is also optional, defaulting to ``None``."""
    original = AssistantMessage(id="a1", role="assistant", content="hello")

    back = messages_from_jsonable(messages_to_jsonable([original]))

    assert back == [original]


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


def test_attachment_refs_survive_a_store_round_trip_on_either_carrier() -> None:
    """A stored thread comes back with its attachment refs, wherever they rode.

    The web component sends them in a user message's ``metadata`` from its
    adoption of AG-UI 1.0 and as an undeclared top-level field before that, and a
    thread written by one is reloaded by the other. The codec keeps the first as
    a declared field and the second as an extra, so both reach the thread
    endpoint's response -- which is where the chips, and the ids the model was
    told about, are restored from.
    """
    refs = [{"id": "a1", "name": "report.pdf", "mime": "application/pdf", "size": 2300}]
    messages = [
        UserMessage.model_validate(
            {"id": "u1", "role": "user", "content": "hi", "metadata": {"attachments": refs}}
        ),
        UserMessage.model_validate(
            {"id": "u2", "role": "user", "content": "and", "attachments": refs}
        ),
    ]

    served = stored_messages_to_wire(messages_to_jsonable(messages))

    assert served[0]["metadata"] == {"attachments": refs}
    assert served[1]["attachments"] == refs
