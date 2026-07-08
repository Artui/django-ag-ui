from __future__ import annotations

import pytest
from ag_ui.core import AssistantMessage, UserMessage
from django.contrib.sessions.backends.cache import SessionStore
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from django_ag_ui.persistence.anonymous_operation_error import AnonymousOperationError
from django_ag_ui.persistence.utils import (
    derive_preview,
    derive_title,
    messages_from_jsonable,
    messages_to_jsonable,
    owner_id_for,
    resolve_owner_id,
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


def _anon_request() -> HttpRequest:
    request = RequestFactory().get("/")

    class _Anon:
        is_authenticated = False
        pk = None

    request.user = _Anon()  # type: ignore[attr-defined]
    return request


def test_resolve_owner_id_returns_pk_for_authenticated_user() -> None:
    request = RequestFactory().get("/")

    class _User:
        is_authenticated = True
        pk = 42

    request.user = _User()  # type: ignore[attr-defined]
    assert resolve_owner_id(request) == "42"


def test_resolve_owner_id_refuses_anonymous_by_default() -> None:
    with pytest.raises(AnonymousOperationError):
        resolve_owner_id(_anon_request())


@override_settings(
    DJANGO_AG_UI={"ALLOW_ANONYMOUS": True},
    SESSION_ENGINE="django.contrib.sessions.backends.cache",
)
def test_resolve_owner_id_buckets_anonymous_by_existing_session() -> None:
    request = _anon_request()
    session = SessionStore()
    session.create()  # a browser that already has a session
    request.session = session  # type: ignore[attr-defined]
    assert resolve_owner_id(request) == f"anon:{session.session_key}"


@override_settings(
    DJANGO_AG_UI={"ALLOW_ANONYMOUS": True},
    SESSION_ENGINE="django.contrib.sessions.backends.cache",
)
def test_resolve_owner_id_creates_a_session_when_the_browser_has_none() -> None:
    request = _anon_request()
    request.session = SessionStore()  # no session_key yet  # type: ignore[attr-defined]
    owner = resolve_owner_id(request)
    assert owner.startswith("anon:")
    assert request.session.session_key is not None


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
