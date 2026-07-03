from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ag_ui.core import UserMessage
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import HttpRequest
from django.test import RequestFactory

from django_ag_ui.persistence.anonymous_operation_error import AnonymousOperationError
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
        self.renamed: list[tuple[str, str]] = []
        self.received_limit: int | None = None

    async def list(
        self, *, request: HttpRequest, limit: int | None = None
    ) -> list[ConversationMeta]:
        self.received_limit = limit
        return self.metas[:limit] if limit is not None else self.metas

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
        return self.conversations.get(thread_id)

    async def exists(self, thread_id: str, *, request: HttpRequest) -> bool:
        return thread_id in self.conversations

    async def delete(self, thread_id: str, *, request: HttpRequest) -> None:
        self.deleted.append(thread_id)

    async def rename(self, thread_id: str, title: str, *, request: HttpRequest) -> None:
        self.renamed.append((thread_id, title))


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


async def test_anonymous_operation_refused_is_403() -> None:
    class _RefusingStore(_FakeStore):
        async def list(
            self, *, request: HttpRequest, limit: int | None = None
        ) -> list[ConversationMeta]:
            raise AnonymousOperationError("anonymous refused")

    response = await ThreadsView(_RefusingStore())(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 403
    assert _body(response) == {"error": "forbidden"}


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
    response = await ThreadsView(_FakeStore())(
        RequestFactory().put("/agent/threads/t1/"), thread_id="t1"
    )
    assert response.status_code == 405


async def test_detail_patch_renames() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = RequestFactory().patch(
        "/agent/threads/t1/", data={"title": "  Trip planning  "}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 200
    assert _body(response) == {"thread_id": "t1", "title": "Trip planning"}
    assert store.renamed == [("t1", "Trip planning")]


async def test_detail_patch_missing_title_is_400() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = RequestFactory().patch(
        "/agent/threads/t1/", data={"title": "   "}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 400
    assert store.renamed == []


async def test_detail_patch_unknown_thread_is_404() -> None:
    store = _FakeStore()  # no conversations → load returns None
    request = RequestFactory().patch(
        "/agent/threads/absent/", data={"title": "x"}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="absent")
    assert response.status_code == 404
    assert store.renamed == []


async def test_detail_patch_invalid_json_is_400() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = RequestFactory().patch(
        "/agent/threads/t1/", data="not json", content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 400
    assert store.renamed == []


async def test_detail_patch_non_object_body_is_400() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = RequestFactory().patch(
        "/agent/threads/t1/", data="[1, 2]", content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 400
    assert store.renamed == []


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


# --- title cap, thread-list limit, existence probe ---


async def test_rename_truncates_an_over_long_title() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = RequestFactory().patch(
        "/agent/threads/t1/", data={"title": "x" * 300}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 200
    assert store.renamed == [("t1", "x" * 255)]
    assert _body(response)["title"] == "x" * 255


async def test_list_defaults_to_the_configured_limit(settings) -> None:
    settings.DJANGO_AG_UI = {"THREAD_LIST_LIMIT": 2}
    store = _FakeStore(metas=[ConversationMeta(thread_id=f"t{i}", title=f"t{i}") for i in range(5)])
    response = await ThreadsView(store)(RequestFactory().get("/agent/threads/"))
    assert store.received_limit == 2
    assert len(_body(response)["threads"]) == 2


async def test_list_query_limit_is_clamped_to_the_ceiling(settings) -> None:
    settings.DJANGO_AG_UI = {"THREAD_LIST_LIMIT": 2}
    store = _FakeStore(metas=[ConversationMeta(thread_id=f"t{i}", title=f"t{i}") for i in range(5)])
    # A smaller ``?limit`` is honored; a larger one is clamped down to the cap.
    await ThreadsView(store)(RequestFactory().get("/agent/threads/?limit=1"))
    assert store.received_limit == 1
    await ThreadsView(store)(RequestFactory().get("/agent/threads/?limit=99"))
    assert store.received_limit == 2


async def test_list_ignores_a_non_positive_or_garbage_limit(settings) -> None:
    settings.DJANGO_AG_UI = {"THREAD_LIST_LIMIT": 3}
    store = _FakeStore()
    for bad in ("0", "-4", "abc"):
        await ThreadsView(store)(RequestFactory().get(f"/agent/threads/?limit={bad}"))
        assert store.received_limit == 3


async def test_rename_probes_exists_without_loading_the_body() -> None:
    # A store whose ``load`` would blow up but whose ``exists`` answers cheaply:
    # the rename must consult ``exists`` only, never deserialize the thread.
    class _NoLoadStore(_FakeStore):
        async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
            raise AssertionError("rename must not load the full conversation body")

    store = _NoLoadStore(conversations={"t1": Conversation(thread_id="t1")})
    request = RequestFactory().patch(
        "/agent/threads/t1/", data={"title": "Renamed"}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 200
    assert store.renamed == [("t1", "Renamed")]
