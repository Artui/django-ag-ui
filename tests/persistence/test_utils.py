from __future__ import annotations

from ag_ui.core import AssistantMessage, UserMessage
from django.test import RequestFactory

from django_ag_ui.persistence.utils import (
    messages_from_jsonable,
    messages_to_jsonable,
    owner_id_for,
)


def test_messages_round_trip() -> None:
    msgs = [
        UserMessage(id="u1", role="user", content="hi"),
        AssistantMessage(id="a1", role="assistant", content="hello"),
    ]
    raw = messages_to_jsonable(msgs)
    assert raw[0]["id"] == "u1"
    back = messages_from_jsonable(raw)
    assert [m.id for m in back] == ["u1", "a1"]
    assert back[0].role == "user"


def test_owner_id_none_when_no_user() -> None:
    assert owner_id_for(RequestFactory().get("/")) is None


def test_owner_id_for_authenticated_user() -> None:
    request = RequestFactory().get("/")

    class _User:
        is_authenticated = True
        pk = 42

    request.user = _User()  # type: ignore[attr-defined]
    assert owner_id_for(request) == "42"


def test_owner_id_none_for_anonymous_user() -> None:
    request = RequestFactory().get("/")

    class _Anon:
        is_authenticated = False
        pk = None

    request.user = _Anon()  # type: ignore[attr-defined]
    assert owner_id_for(request) is None
