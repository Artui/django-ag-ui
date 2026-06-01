from __future__ import annotations

from django.test import RequestFactory

from django_ag_ui.persistence.null_conversation_store import NullConversationStore
from django_ag_ui.persistence.types.conversation import Conversation


async def test_null_store_is_a_noop() -> None:
    store = NullConversationStore()
    request = RequestFactory().get("/")
    assert await store.load("t1", request=request) is None
    assert await store.save(Conversation(thread_id="t1"), request=request) is None
    assert await store.delete("t1", request=request) is None
