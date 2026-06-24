from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ag_ui.core import UserMessage
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import HttpRequest
from django.test import RequestFactory

from django_ag_ui.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMeta


class _FakeStore:
    """A minimal in-memory store exercising the view without a DB."""

    def __init__(
        self,
        *,
        metas: list[ConversationMeta] | None = None,
        conversations: dict[str, Conversation] | None = None,
    ) -> None:
        self.metas = metas or []
        self.conversations = conversations or {}
        self.deleted: list[str] = []

    async def list(self, *, request: HttpRequest) -> list[ConversationMeta]:
        return self.metas

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
        return self.conversations.get(thread_id)

    async def delete(self, thread_id: str, *, request: HttpRequest) -> None:
        self.deleted.append(thread_id)


def _body(response: Any) -> Any:
    return json.loads(response.content)


async def test_list_returns_metadata_only() -> None:
    store = _FakeStore(
        metas=[
            ConversationMeta(
                thread_id="t1",
                title="Hi",
                updated_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
                preview="hello there",
                owner_id="7",
            ),
            ConversationMeta(thread_id="t2", title="New conversation"),
        ]
    )
    response = await ThreadsView(store)(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 200
    assert _body(response) == {
        "threads": [
            {
                "thread_id": "t1",
                "title": "Hi",
                "updated_at": "2026-06-24T00:00:00+00:00",
                "preview": "hello there",
            },
            {"thread_id": "t2", "title": "New conversation", "updated_at": None, "preview": ""},
        ]
    }


async def test_detail_get_returns_messages() -> None:
    store = _FakeStore(
        conversations={
            "t1": Conversation(
                thread_id="t1", messages=[UserMessage(id="u1", role="user", content="hi")]
            )
        }
    )
    response = await ThreadsView(store)(RequestFactory().get("/agent/threads/t1/"), thread_id="t1")
    assert response.status_code == 200
    payload = _body(response)
    assert payload["thread_id"] == "t1"
    assert [m["id"] for m in payload["messages"]] == ["u1"]


async def test_detail_get_missing_is_404() -> None:
    response = await ThreadsView(_FakeStore())(
        RequestFactory().get("/agent/threads/absent/"), thread_id="absent"
    )
    assert response.status_code == 404
    assert _body(response) == {"error": "not found"}


async def test_detail_delete_removes_thread() -> None:
    store = _FakeStore()
    response = await ThreadsView(store)(
        RequestFactory().delete("/agent/threads/t1/"), thread_id="t1"
    )
    assert response.status_code == 204
    assert store.deleted == ["t1"]


async def test_collection_rejects_non_get() -> None:
    response = await ThreadsView(_FakeStore())(RequestFactory().post("/agent/threads/"))
    assert response.status_code == 405


async def test_detail_rejects_unsupported_method() -> None:
    # PATCH (rename) is a planned follow-up — unsupported for now.
    response = await ThreadsView(_FakeStore())(
        RequestFactory().patch("/agent/threads/t1/"), thread_id="t1"
    )
    assert response.status_code == 405


async def test_anonymous_rejected_when_require_authenticated() -> None:
    view = ThreadsView(_FakeStore(), require_authenticated=True)
    response = await view(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 401


async def test_get_user_hook_opens_the_endpoint() -> None:
    from types import SimpleNamespace

    view = ThreadsView(
        _FakeStore(),
        require_authenticated=True,
        get_user=lambda _request: SimpleNamespace(is_authenticated=True),
    )
    response = await view(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 200


async def test_round_trips_against_the_session_store() -> None:
    store = DjangoSessionConversationStore()
    request = RequestFactory().get("/agent/threads/")
    request.session = SessionStore()  # type: ignore[attr-defined]
    await store.save(
        Conversation(thread_id="t1", messages=[UserMessage(id="u1", role="user", content="hello")]),
        request=request,
    )
    view = ThreadsView(store)

    listed = await view(request)
    assert [row["thread_id"] for row in _body(listed)["threads"]] == ["t1"]

    detail = await view(request, thread_id="t1")
    assert _body(detail)["thread_id"] == "t1"
    assert [m["id"] for m in _body(detail)["messages"]] == ["u1"]
