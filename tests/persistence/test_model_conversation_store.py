from __future__ import annotations

from types import SimpleNamespace

from django.http import HttpRequest
from django.test import RequestFactory

from django_ag_ui.persistence.model_conversation_store import ModelConversationStore
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMeta


def _authed_request(pk: str = "7") -> HttpRequest:
    """A request whose user resolves to ``owner_id == pk`` (no DB / session)."""
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=True, pk=pk)  # type: ignore[attr-defined]
    return request


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
    request = _authed_request()
    conv = Conversation(thread_id="t1")

    await store.save(conv, request=request)
    assert await store.load("t1", request=request) is conv
    assert ("7", "t1") in store.rows  # scoped to the resolved owner id

    await store.delete("t1", request=request)
    assert await store.load("t1", request=request) is None


async def test_list_defaults_to_empty_for_subclasses_that_dont_override() -> None:
    # ``_DictStore`` doesn't override ``_list`` — listing stays opt-in.
    assert await _DictStore().list(request=_authed_request()) == []


class _ListableStore(_DictStore):
    def _list(self, owner_id: str | None, limit: int | None) -> list[ConversationMeta]:
        metas = [
            ConversationMeta(thread_id=thread_id, title=thread_id, owner_id=owner)
            for (owner, thread_id) in self.rows
            if owner == owner_id
        ]
        return metas[:limit] if limit is not None else metas


async def test_list_uses_subclass_override_and_owner_scoping() -> None:
    store = _ListableStore()
    request = _authed_request()
    await store.save(Conversation(thread_id="t1"), request=request)
    await store.save(Conversation(thread_id="t2"), request=request)
    store.rows[("99", "other")] = Conversation(thread_id="other")  # a different owner

    metas = await store.list(request=request)
    assert sorted(meta.thread_id for meta in metas) == ["t1", "t2"]


async def test_rename_defaults_to_noop_for_subclasses_that_dont_override() -> None:
    assert await _DictStore().rename("t1", "x", request=_authed_request()) is None


class _RenamableStore(_DictStore):
    def __init__(self) -> None:
        super().__init__()
        self.renames: list[tuple[str | None, str, str]] = []

    def _rename(self, thread_id: str, title: str, owner_id: str | None) -> None:
        self.renames.append((owner_id, thread_id, title))


async def test_rename_uses_subclass_override_with_owner_scoping() -> None:
    store = _RenamableStore()
    await store.rename("t1", "Renamed", request=_authed_request())
    assert store.renames == [("7", "t1", "Renamed")]


async def test_exists_defaults_to_the_fetch_probe() -> None:
    store = _DictStore()
    request = _authed_request()
    await store.save(Conversation(thread_id="t1"), request=request)
    assert await store.exists("t1", request=request) is True
    assert await store.exists("absent", request=request) is False
