from __future__ import annotations

import json

import pytest
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.persistence.scoped_conversation_store import ScopedConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.thread_ref import ThreadRef

from django_ag_ui.persistence.threads_view import ThreadsView
from tests.authed_request_factory import AuthedAsyncRequestFactory

pytestmark = pytest.mark.django_db(transaction=True)


async def test_a_child_row_names_its_parent_in_the_parents_scope() -> None:
    request = AuthedAsyncRequestFactory().get("/agent/threads/")
    parents = ScopedConversationStore(DefaultConversationStore(), scope="a")
    children = ScopedConversationStore(DefaultConversationStore(), scope="b")
    await parents.save(Conversation("p", []), request=request)
    await children.save(Conversation("c", [], parent=ThreadRef("p", "a")), request=request)
    await children.save(Conversation("own", []), request=request)

    response = await ThreadsView(children)(request)

    rows = {row["thread_id"]: row for row in json.loads(response.content)["threads"]}
    assert rows["c"]["parent"] == {"thread_id": "p", "scope": "a"}
    assert rows["own"]["parent"] is None


async def test_a_parent_in_an_unscoped_store_has_no_scope() -> None:
    request = AuthedAsyncRequestFactory().get("/agent/threads/")
    store = DefaultConversationStore()
    await store.save(Conversation("c", [], parent=ThreadRef("p")), request=request)

    response = await ThreadsView(store)(request)

    assert json.loads(response.content)["threads"][0]["parent"] == {"thread_id": "p", "scope": None}
