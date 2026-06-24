from __future__ import annotations

import pytest
from ag_ui.core import UserMessage

from django_ag_ui.contrib.store.default_conversation_store import DefaultConversationStore
from django_ag_ui.contrib.store.models import StoredConversation
from django_ag_ui.persistence.types.conversation import Conversation

pytestmark = pytest.mark.django_db


def _conv(
    thread_id: str = "t1", text: str = "book a flight", owner_id: str | None = "7"
) -> Conversation:
    return Conversation(
        thread_id=thread_id,
        messages=[UserMessage(id="u1", role="user", content=text)],
        owner_id=owner_id,
    )


def test_store_then_fetch_round_trips() -> None:
    store = DefaultConversationStore()
    store._store(_conv(), "7")
    fetched = store._fetch("t1", "7")
    assert fetched is not None
    assert fetched.thread_id == "t1"
    assert [m.id for m in fetched.messages] == ["u1"]
    assert fetched.owner_id == "7"


def test_fetch_missing_returns_none() -> None:
    assert DefaultConversationStore()._fetch("absent", "7") is None


def test_store_denormalizes_title_and_preview() -> None:
    store = DefaultConversationStore()
    store._store(_conv(text="book a flight"), "7")
    (meta,) = store._list("7")
    assert meta.thread_id == "t1"
    assert meta.title == "book a flight"
    assert meta.preview == "book a flight"
    assert meta.updated_at is not None
    assert meta.owner_id == "7"


def test_list_is_owner_scoped() -> None:
    store = DefaultConversationStore()
    store._store(_conv("t1"), "7")
    store._store(_conv("t2"), "7")
    store._store(_conv("t3"), "99")  # a different owner
    assert {meta.thread_id for meta in store._list("7")} == {"t1", "t2"}


def test_resave_preserves_title_and_refreshes_preview() -> None:
    store = DefaultConversationStore()
    store._store(_conv(text="book a flight"), "7")
    store._store(
        Conversation(
            thread_id="t1",
            messages=[
                UserMessage(id="u1", role="user", content="book a flight"),
                UserMessage(id="u2", role="user", content="actually, cancel it"),
            ],
            owner_id="7",
        ),
        "7",
    )
    (meta,) = store._list("7")
    assert meta.title == "book a flight"  # title frozen at first message
    assert meta.preview == "actually, cancel it"  # preview follows the latest


def test_rename_updates_title() -> None:
    store = DefaultConversationStore()
    store._store(_conv(), "7")
    store._rename("t1", "Trip planning", "7")
    (meta,) = store._list("7")
    assert meta.title == "Trip planning"


def test_remove_deletes() -> None:
    store = DefaultConversationStore()
    store._store(_conv(), "7")
    store._remove("t1", "7")
    assert store._fetch("t1", "7") is None


def test_anonymous_owner_normalized_to_empty_string() -> None:
    store = DefaultConversationStore()
    store._store(_conv(owner_id=None), None)
    fetched = store._fetch("t1", None)
    assert fetched is not None
    assert fetched.owner_id is None  # stored "" maps back to None
    assert StoredConversation.objects.get(thread_id="t1").owner_id == ""
    (meta,) = store._list(None)
    assert meta.owner_id is None
