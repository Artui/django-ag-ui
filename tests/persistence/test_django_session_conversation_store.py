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


async def test_list_enumerates_session_threads_with_metadata() -> None:
    store = DjangoSessionConversationStore()
    request = _request()
    await store.save(
        Conversation(
            thread_id="t1",
            messages=[UserMessage(id="u1", role="user", content="book a flight")],
            owner_id="7",
        ),
        request=request,
    )
    await store.save(
        Conversation(
            thread_id="t2",
            messages=[UserMessage(id="u2", role="user", content="cancel it")],
        ),
        request=request,
    )

    metas = {meta.thread_id: meta for meta in await store.list(request=request)}
    assert set(metas) == {"t1", "t2"}
    assert metas["t1"].title == "book a flight"
    assert metas["t1"].preview == "book a flight"
    assert metas["t1"].owner_id == "7"
    # ``updated_at`` is stamped on save (no bodies needed to sort the drawer).
    assert metas["t1"].updated_at is not None


async def test_list_empty_session_is_empty() -> None:
    assert await DjangoSessionConversationStore().list(request=_request()) == []


async def test_list_tolerates_legacy_entry_without_timestamp() -> None:
    store = DjangoSessionConversationStore()
    request = _request()
    # A row written before updated_at tracking — no "updated_at" key.
    request.session["django_ag_ui_conversations"] = {"old": {"messages": [], "owner_id": None}}
    (meta,) = await store.list(request=request)
    assert meta.thread_id == "old"
    assert meta.updated_at is None
    assert meta.title == "New conversation"
