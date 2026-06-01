from __future__ import annotations

from django.test import RequestFactory

from django_ag_ui.persistence.model_conversation_store import ModelConversationStore
from django_ag_ui.persistence.types.conversation import Conversation


class _DictStore(ModelConversationStore):
    """A dict-backed subclass exercising the base's async + owner plumbing."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str | None, str], Conversation] = {}

    def _fetch(self, thread_id: str, owner_id: str | None) -> Conversation | None:
        return self.rows.get((owner_id, thread_id))

    def _store(self, conversation: Conversation, owner_id: str | None) -> None:
        self.rows[(owner_id, conversation.thread_id)] = conversation

    def _remove(self, thread_id: str, owner_id: str | None) -> None:
        self.rows.pop((owner_id, thread_id), None)


async def test_base_wraps_sync_ops_with_owner_scoping() -> None:
    store = _DictStore()
    request = RequestFactory().get("/")  # anonymous → owner_id None
    conv = Conversation(thread_id="t1")

    await store.save(conv, request=request)
    assert await store.load("t1", request=request) is conv

    await store.delete("t1", request=request)
    assert await store.load("t1", request=request) is None
