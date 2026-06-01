from __future__ import annotations

from ag_ui.core import UserMessage
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import HttpRequest
from django.test import RequestFactory

from django_ag_ui.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_ag_ui.persistence.types.conversation import Conversation


def _request() -> HttpRequest:
    request = RequestFactory().get("/")
    request.session = SessionStore()  # type: ignore[attr-defined]
    return request


async def test_save_then_load_round_trips() -> None:
    store = DjangoSessionConversationStore()
    request = _request()
    conv = Conversation(
        thread_id="t1",
        messages=[UserMessage(id="u1", role="user", content="hi")],
        owner_id="7",
    )
    await store.save(conv, request=request)

    loaded = await store.load("t1", request=request)
    assert loaded is not None
    assert loaded.thread_id == "t1"
    assert [m.id for m in loaded.messages] == ["u1"]
    assert loaded.owner_id == "7"


async def test_load_missing_returns_none() -> None:
    assert await DjangoSessionConversationStore().load("absent", request=_request()) is None


async def test_delete_removes_conversation() -> None:
    store = DjangoSessionConversationStore()
    request = _request()
    await store.save(Conversation(thread_id="t1"), request=request)
    await store.delete("t1", request=request)
    assert await store.load("t1", request=request) is None


async def test_delete_missing_is_a_noop() -> None:
    store = DjangoSessionConversationStore()
    request = _request()
    await store.delete("absent", request=request)
    assert await store.load("absent", request=request) is None
