from __future__ import annotations

import pytest
from django.test import RequestFactory

from django_ag_ui.persistence.scoped_conversation_store import ScopedConversationStore
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMeta


class _MemoryStore:
    """A store keyed exactly the way the real ones are: by thread id."""

    def __init__(self) -> None:
        self.rows: dict[str, Conversation] = {}
        self.titles: dict[str, str] = {}

    async def load(self, thread_id, *, request):  # noqa: ANN001, ANN202, ARG002
        return self.rows.get(thread_id)

    async def save(self, conversation, *, request):  # noqa: ANN001, ANN202, ARG002
        self.rows[conversation.thread_id] = conversation

    async def delete(self, thread_id, *, request):  # noqa: ANN001, ANN202, ARG002
        self.rows.pop(thread_id, None)

    async def list(self, *, request, limit=None):  # noqa: ANN001, ANN202, ARG002
        return [ConversationMeta(thread_id=t, title=t) for t in self.rows]

    async def exists(self, thread_id, *, request):  # noqa: ANN001, ANN202, ARG002
        return thread_id in self.rows

    async def rename(self, thread_id, title, *, request):  # noqa: ANN001, ANN202, ARG002
        self.titles[thread_id] = title


@pytest.fixture
def request_() -> object:
    return RequestFactory().post("/agent/")


async def test_two_scopes_over_one_store_do_not_see_each_others_threads(request_) -> None:
    """The gap this closes: a thread started at /internal/agent showed up in
    /public/agent's history drawer, and could be resumed there under the public
    agent's model, tools and guard policy."""
    inner = _MemoryStore()
    internal = ScopedConversationStore(inner, scope="internal")
    public = ScopedConversationStore(inner, scope="public")

    await internal.save(Conversation(thread_id="t1", messages=[]), request=request_)

    assert await internal.load("t1", request=request_) is not None
    assert await public.load("t1", request=request_) is None
    assert await public.exists("t1", request=request_) is False
    assert [m.thread_id for m in await public.list(request=request_)] == []


async def test_the_scope_never_reaches_the_client(request_) -> None:
    """Only the storage key is prefixed — the client's ids round-trip unchanged."""
    inner = _MemoryStore()
    scoped = ScopedConversationStore(inner, scope="internal")

    await scoped.save(Conversation(thread_id="t1", messages=[]), request=request_)

    assert "internal:t1" in inner.rows  # prefixed at rest
    loaded = await scoped.load("t1", request=request_)
    assert loaded is not None
    assert loaded.thread_id == "t1"  # unprefixed on the way out
    assert [m.thread_id for m in await scoped.list(request=request_)] == ["t1"]


async def test_delete_and_rename_are_scoped(request_) -> None:
    inner = _MemoryStore()
    internal = ScopedConversationStore(inner, scope="internal")
    public = ScopedConversationStore(inner, scope="public")
    await internal.save(Conversation(thread_id="t1", messages=[]), request=request_)

    # A same-id delete from the other scope must not touch it.
    await public.delete("t1", request=request_)
    assert await internal.exists("t1", request=request_) is True

    await internal.rename("t1", "Renamed", request=request_)
    assert inner.titles == {"internal:t1": "Renamed"}

    await internal.delete("t1", request=request_)
    assert await internal.exists("t1", request=request_) is False


async def test_an_unprefixed_row_is_left_alone(request_) -> None:
    """Rows written before the wrapper was introduced keep their own ids — the
    unkey is a strip, not a slice."""
    inner = _MemoryStore()
    inner.rows["legacy"] = Conversation(thread_id="legacy", messages=[])
    scoped = ScopedConversationStore(inner, scope="internal")

    # Not this scope's row, so it isn't listed...
    assert [m.thread_id for m in await scoped.list(request=request_)] == []
    # ...and the id is untouched in the store.
    assert "legacy" in inner.rows
