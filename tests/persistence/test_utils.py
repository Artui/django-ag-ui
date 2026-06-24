from __future__ import annotations

from ag_ui.core import AssistantMessage, UserMessage
from django.test import RequestFactory

from django_ag_ui.persistence.utils import (
    derive_preview,
    derive_title,
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


def test_derive_title_from_first_user_message() -> None:
    messages = [
        AssistantMessage(id="a0", role="assistant", content="welcome"),
        UserMessage(id="u1", role="user", content="  book a   flight  "),
        UserMessage(id="u2", role="user", content="to paris"),
    ]
    # First *user* message wins, whitespace collapsed.
    assert derive_title(messages) == "book a flight"


def test_derive_title_falls_back_without_user_text() -> None:
    assert derive_title([AssistantMessage(id="a0", role="assistant", content="hi")]) == (
        "New conversation"
    )
    # A user message with only whitespace is skipped, not titled.
    assert derive_title([UserMessage(id="u1", role="user", content="   ")]) == "New conversation"


def test_derive_title_truncates_long_text() -> None:
    title = derive_title([UserMessage(id="u1", role="user", content="x" * 100)])
    assert len(title) == 60
    assert title.endswith("…")


def test_derive_preview_uses_latest_text() -> None:
    messages = [
        UserMessage(id="u1", role="user", content="first"),
        AssistantMessage(id="a1", role="assistant", content="last reply"),
    ]
    assert derive_preview(messages) == "last reply"


def test_derive_preview_empty_when_no_text() -> None:
    assert derive_preview([]) == ""
    # A message whose content is None contributes no preview text.
    assert derive_preview([AssistantMessage(id="a1", role="assistant", content=None)]) == ""
