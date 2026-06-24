from __future__ import annotations

from django.test import RequestFactory

from django_ag_ui.persistence.model_conversation_store import ModelConversationStore
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMeta


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


async def test_list_defaults_to_empty_for_subclasses_that_dont_override() -> None:
    # ``_DictStore`` doesn't override ``_list`` — listing stays opt-in.
    assert await _DictStore().list(request=RequestFactory().get("/")) == []


class _ListableStore(_DictStore):
    def _list(self, owner_id: str | None) -> list[ConversationMeta]:
        return [
            ConversationMeta(thread_id=thread_id, title=thread_id, owner_id=owner)
            for (owner, thread_id) in self.rows
            if owner == owner_id
        ]


async def test_list_uses_subclass_override_and_owner_scoping() -> None:
    store = _ListableStore()
    request = RequestFactory().get("/")  # anonymous → owner_id None
    await store.save(Conversation(thread_id="t1"), request=request)
    await store.save(Conversation(thread_id="t2"), request=request)
    store.rows[("99", "other")] = Conversation(thread_id="other")  # a different owner

    metas = await store.list(request=request)
    assert sorted(meta.thread_id for meta in metas) == ["t1", "t2"]
