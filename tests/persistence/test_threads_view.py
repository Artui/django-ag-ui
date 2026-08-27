from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from ag_ui.core import ActivitySnapshotEvent, Message
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import HttpRequest
from django.test import RequestFactory
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from django_pydantic_agent.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_meta import ConversationMeta

from django_ag_ui.agent.chart_activity import CHART_ACTIVITY_TYPE, chart_activity
from django_ag_ui.agent.types.chart_series import ChartSeries
from django_ag_ui.agent.types.chart_spec import ChartSpec
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.types.thread_activity import ThreadActivity
from tests.authed_request_factory import AuthedRequestFactory


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
    response = await ThreadsView(store)(AuthedRequestFactory().get("/agent/threads/"))
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
                thread_id="t1", messages=[{"id": "u1", "role": "user", "content": "hi"}]
            )
        }
    )
    response = await ThreadsView(store)(
        AuthedRequestFactory().get("/agent/threads/t1/"), thread_id="t1"
    )
    assert response.status_code == 200
    payload = _body(response)
    assert payload["thread_id"] == "t1"
    assert [m["id"] for m in payload["messages"]] == ["u1"]


async def test_detail_get_serves_the_protocol_key_names() -> None:
    """A thread stored in the old spelling still reaches the client usable.

    The endpoint used to hand storage records straight out, which meant a
    client looking for ``toolCalls`` found ``tool_calls`` and rendered a
    transcript with every tool call and result missing.
    """
    store = _FakeStore(
        conversations={
            "t1": Conversation(
                thread_id="t1",
                messages=[
                    {
                        "id": "a1",
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "read_page", "arguments": "{}"},
                            }
                        ],
                    },
                    {"id": "t1", "role": "tool", "content": "{}", "tool_call_id": "call_1"},
                ],
            )
        }
    )
    response = await ThreadsView(store)(
        AuthedRequestFactory().get("/agent/threads/t1/"), thread_id="t1"
    )
    messages = _body(response)["messages"]
    assert messages[0]["toolCalls"][0]["id"] == "call_1"
    assert messages[1]["toolCallId"] == "call_1"


async def test_anonymous_operation_refused_is_403() -> None:
    # Only reachable with authentication deliberately waived: otherwise the
    # anonymous request never gets past the view to the store that refuses it.
    class _RefusingStore(_FakeStore):
        async def list(
            self, *, request: HttpRequest, limit: int | None = None
        ) -> list[ConversationMeta]:
            raise AnonymousOperationError("anonymous refused")

    view = ThreadsView(_RefusingStore(), require_authenticated=False)
    response = await view(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 403
    assert _body(response) == {"error": "forbidden"}


async def test_detail_get_missing_is_404() -> None:
    response = await ThreadsView(_FakeStore())(
        AuthedRequestFactory().get("/agent/threads/absent/"), thread_id="absent"
    )
    assert response.status_code == 404
    assert _body(response) == {"error": "not found"}


async def test_detail_delete_removes_thread() -> None:
    store = _FakeStore()
    response = await ThreadsView(store)(
        AuthedRequestFactory().delete("/agent/threads/t1/"), thread_id="t1"
    )
    assert response.status_code == 204
    assert store.deleted == ["t1"]


async def test_collection_rejects_non_get() -> None:
    response = await ThreadsView(_FakeStore())(AuthedRequestFactory().post("/agent/threads/"))
    assert response.status_code == 405


async def test_detail_rejects_unsupported_method() -> None:
    response = await ThreadsView(_FakeStore())(
        AuthedRequestFactory().put("/agent/threads/t1/"), thread_id="t1"
    )
    assert response.status_code == 405


async def test_detail_patch_renames() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = AuthedRequestFactory().patch(
        "/agent/threads/t1/", data={"title": "  Trip planning  "}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 200
    assert _body(response) == {"thread_id": "t1", "title": "Trip planning"}
    assert store.renamed == [("t1", "Trip planning")]


async def test_detail_patch_missing_title_is_400() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = AuthedRequestFactory().patch(
        "/agent/threads/t1/", data={"title": "   "}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 400
    assert store.renamed == []


async def test_detail_patch_unknown_thread_is_404() -> None:
    store = _FakeStore()  # no conversations → load returns None
    request = AuthedRequestFactory().patch(
        "/agent/threads/absent/", data={"title": "x"}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="absent")
    assert response.status_code == 404
    assert store.renamed == []


async def test_detail_patch_invalid_json_is_400() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = AuthedRequestFactory().patch(
        "/agent/threads/t1/", data="not json", content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 400
    assert store.renamed == []


async def test_detail_patch_non_object_body_is_400() -> None:
    store = _FakeStore(conversations={"t1": Conversation(thread_id="t1")})
    request = AuthedRequestFactory().patch(
        "/agent/threads/t1/", data="[1, 2]", content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 400
    assert store.renamed == []


async def test_anonymous_rejected_by_default() -> None:
    response = await ThreadsView(_FakeStore())(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 401


async def test_anonymous_served_when_authentication_is_waived() -> None:
    view = ThreadsView(_FakeStore(), require_authenticated=False)
    response = await view(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 200


async def test_get_user_hook_opens_the_endpoint() -> None:
    from types import SimpleNamespace

    view = ThreadsView(
        _FakeStore(),
        get_user=lambda _request: SimpleNamespace(is_authenticated=True),
    )
    response = await view(RequestFactory().get("/agent/threads/"))
    assert response.status_code == 200


async def test_round_trips_against_the_session_store() -> None:
    store = DjangoSessionConversationStore()
    request = AuthedRequestFactory().get("/agent/threads/")
    request.session = SessionStore()  # type: ignore[attr-defined]
    await store.save(
        Conversation(thread_id="t1", messages=[{"id": "u1", "role": "user", "content": "hello"}]),
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
    request = AuthedRequestFactory().patch(
        "/agent/threads/t1/", data={"title": "x" * 300}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 200
    assert store.renamed == [("t1", "x" * 255)]
    assert _body(response)["title"] == "x" * 255


async def test_list_defaults_to_the_configured_limit(settings) -> None:
    settings.DJANGO_AG_UI = {"THREAD_LIST_LIMIT": 2}
    store = _FakeStore(metas=[ConversationMeta(thread_id=f"t{i}", title=f"t{i}") for i in range(5)])
    response = await ThreadsView(store)(AuthedRequestFactory().get("/agent/threads/"))
    assert store.received_limit == 2
    assert len(_body(response)["threads"]) == 2


async def test_list_query_limit_is_clamped_to_the_ceiling(settings) -> None:
    settings.DJANGO_AG_UI = {"THREAD_LIST_LIMIT": 2}
    store = _FakeStore(metas=[ConversationMeta(thread_id=f"t{i}", title=f"t{i}") for i in range(5)])
    # A smaller ``?limit`` is honored; a larger one is clamped down to the cap.
    await ThreadsView(store)(AuthedRequestFactory().get("/agent/threads/?limit=1"))
    assert store.received_limit == 1
    await ThreadsView(store)(AuthedRequestFactory().get("/agent/threads/?limit=99"))
    assert store.received_limit == 2


async def test_list_ignores_a_non_positive_or_garbage_limit(settings) -> None:
    settings.DJANGO_AG_UI = {"THREAD_LIST_LIMIT": 3}
    store = _FakeStore()
    for bad in ("0", "-4", "abc"):
        await ThreadsView(store)(AuthedRequestFactory().get(f"/agent/threads/?limit={bad}"))
        assert store.received_limit == 3


async def test_rename_probes_exists_without_loading_the_body() -> None:
    # A store whose ``load`` would blow up but whose ``exists`` answers cheaply:
    # the rename must consult ``exists`` only, never deserialize the thread.
    class _NoLoadStore(_FakeStore):
        async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
            raise AssertionError("rename must not load the full conversation body")

    store = _NoLoadStore(conversations={"t1": Conversation(thread_id="t1")})
    request = AuthedRequestFactory().patch(
        "/agent/threads/t1/", data={"title": "Renamed"}, content_type="application/json"
    )
    response = await ThreadsView(store)(request, thread_id="t1")
    assert response.status_code == 200
    assert store.renamed == [("t1", "Renamed")]


class _StubActivitySource:
    """A source returning a fixed list, recording what the view handed it."""

    def __init__(self, activities: list[ThreadActivity]) -> None:
        self._activities = activities
        self.seen_thread_id: str | None = None
        self.seen_message_ids: list[str] = []
        self.seen_request: HttpRequest | None = None

    async def activities_for(
        self, thread_id: str, *, messages: Sequence[Message], request: HttpRequest
    ) -> Sequence[ThreadActivity]:
        self.seen_thread_id = thread_id
        self.seen_message_ids = [message.id for message in messages]
        self.seen_request = request
        return self._activities


def _chart(chart_id: str, label: str) -> ActivitySnapshotEvent:
    return chart_activity(
        ChartSpec(kind="bar", labels=("mon",), series=(ChartSeries(label, (1.0,)),)),
        chart_id=chart_id,
    )


def _two_turn_store() -> _FakeStore:
    return _FakeStore(
        conversations={
            "t1": Conversation(
                thread_id="t1",
                messages=[
                    {"id": "u1", "role": "user", "content": "chart it"},
                    {"id": "a1", "role": "assistant", "content": "Here you go."},
                    {"id": "u2", "role": "user", "content": "again"},
                    {"id": "a2", "role": "assistant", "content": "Done."},
                ],
            )
        }
    )


async def _read_thread(source: _StubActivitySource) -> Any:
    view = ThreadsView(_two_turn_store(), thread_activity_source=source)
    response = await view(AuthedRequestFactory().get("/agent/threads/t1/"), thread_id="t1")
    assert response.status_code == 200
    return _body(response)


async def test_no_activity_source_leaves_the_stored_thread_alone() -> None:
    """The default, and the reason the seam costs nothing when unused."""
    view = ThreadsView(_two_turn_store())
    response = await view(AuthedRequestFactory().get("/agent/threads/t1/"), thread_id="t1")
    assert [m["id"] for m in _body(response)["messages"]] == ["u1", "a1", "u2", "a2"]


async def test_anchored_activity_lands_after_its_message() -> None:
    source = _StubActivitySource([ThreadActivity(_chart("c1", "signups"), after_message_id="a1")])
    payload = await _read_thread(source)
    assert [m["id"] for m in payload["messages"]] == ["u1", "a1", "c1", "u2", "a2"]


async def test_activity_reaches_the_wire_in_the_protocol_spelling() -> None:
    """``activityType``, not ``activity_type``.

    The web component's replay reads the protocol's camelCase key and ignores a
    message whose type it cannot see, so a restored chart would be a message in
    the payload that draws nothing — the same class of defect that once cost a
    released version of the component on ``toolCalls``.
    """
    source = _StubActivitySource([ThreadActivity(_chart("c1", "signups"))])
    restored = (await _read_thread(source))["messages"][-1]
    assert restored["role"] == "activity"
    assert restored["activityType"] == CHART_ACTIVITY_TYPE
    assert restored["content"]["series"] == [{"label": "signups", "points": [1.0]}]


async def test_unanchored_activity_lands_at_the_end() -> None:
    source = _StubActivitySource([ThreadActivity(_chart("c1", "signups"))])
    payload = await _read_thread(source)
    assert [m["id"] for m in payload["messages"]] == ["u1", "a1", "u2", "a2", "c1"]


async def test_unknown_anchor_falls_back_to_the_end() -> None:
    """An anchor the thread has lost keeps the chart, at the price of position.

    Dropping it instead would reintroduce the silently-missing chart this whole
    seam exists to fix.
    """
    source = _StubActivitySource([ThreadActivity(_chart("c1", "signups"), after_message_id="gone")])
    payload = await _read_thread(source)
    assert [m["id"] for m in payload["messages"]] == ["u1", "a1", "u2", "a2", "c1"]


async def test_repeated_id_keeps_the_first_position_and_the_last_content() -> None:
    """A source replaying an append-only log sends a chart and its revision.

    One chart moved; two blocks would read as two measurements. The client
    replaces in place, which keeps where the first one sat, so collapsing here
    agrees with it and sends less.
    """
    source = _StubActivitySource(
        [
            ThreadActivity(_chart("c1", "before"), after_message_id="a1"),
            ThreadActivity(_chart("c1", "after"), after_message_id="a2"),
        ]
    )
    payload = await _read_thread(source)
    assert [m["id"] for m in payload["messages"]] == ["u1", "a1", "c1", "u2", "a2"]
    assert payload["messages"][2]["content"]["series"] == [{"label": "after", "points": [1.0]}]


async def test_several_activities_share_one_anchor_in_order() -> None:
    source = _StubActivitySource(
        [
            ThreadActivity(_chart("c1", "first"), after_message_id="a1"),
            ThreadActivity(_chart("c2", "second"), after_message_id="a1"),
        ]
    )
    payload = await _read_thread(source)
    assert [m["id"] for m in payload["messages"]] == ["u1", "a1", "c1", "c2", "u2", "a2"]


async def test_the_merge_never_writes_back_to_the_store() -> None:
    """The invariant the whole seam was chosen to keep.

    Persisting activities beside the model history is the road not taken here:
    it needs an ordering rule, a dedup rule and an answer for what a resumed
    run does with a snapshot that already contains them. Merging on read costs
    none of that only if the stored thread really is untouched, so this asserts
    it against a real store rather than a double that could not write anyway.
    """
    store = DjangoSessionConversationStore()
    request = AuthedRequestFactory().get("/agent/threads/t1/")
    request.session = SessionStore()  # type: ignore[attr-defined]
    await store.save(
        Conversation(thread_id="t1", messages=[{"id": "u1", "role": "user", "content": "hi"}]),
        request=request,
    )
    source = _StubActivitySource([ThreadActivity(_chart("c1", "signups"))])

    served = await ThreadsView(store, thread_activity_source=source)(request, thread_id="t1")

    assert [m["id"] for m in _body(served)["messages"]] == ["u1", "c1"]
    stored = await store.load("t1", request=request)
    assert stored is not None
    assert [m["id"] for m in stored.messages] == ["u1"]


async def test_source_is_handed_the_stored_thread_and_the_request() -> None:
    """What makes ``after_message_id`` answerable: the ids are in scope."""
    source = _StubActivitySource([])
    await _read_thread(source)
    assert source.seen_thread_id == "t1"
    assert source.seen_message_ids == ["u1", "a1", "u2", "a2"]
    assert source.seen_request is not None
    assert source.seen_request.path == "/agent/threads/t1/"
