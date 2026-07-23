from __future__ import annotations

from ag_ui.core import AssistantMessage, UserMessage

from django_ag_ui.persistence.utils import messages_from_jsonable, messages_to_jsonable


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
