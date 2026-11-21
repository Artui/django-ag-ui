from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_waiting import RunWaiting
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.persistence import threads_view as threads_view_module
from django_ag_ui.persistence.thread_read_view import ThreadReadView
from tests.authed_request_factory import AuthedAsyncRequestFactory, authenticated_user

pytestmark = pytest.mark.django_db(transaction=True)

_LEASE = timedelta(minutes=5)
_SSE = {"Accept": "text/event-stream"}


def _server(run_store: Any = None, **kwargs: Any) -> AGUIServer:
    return AGUIServer(
        ToolRegistry(),
        model=TestModel(),
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        run_store=run_store,
        config=build_ag_ui_config(heartbeat_seconds=0),
        **kwargs,
    )


def _route(server: AGUIServer, name: str) -> Any:
    patterns, _, _ = server.urls
    return next(p for p in patterns if p.name == name).callback


async def _thread(*thread_ids: str) -> Any:
    request = AuthedAsyncRequestFactory().get("/")
    store = DefaultConversationStore()
    for thread_id in thread_ids:
        await store.save(Conversation(thread_id=thread_id, messages=[]), request=request)
    return request


async def _list(server: AGUIServer, **headers: str) -> Any:
    request = AuthedAsyncRequestFactory().get("/agent/threads/", headers=headers)
    return await _route(server, "threads")(request)


def _rows(response: Any) -> dict[str, dict[str, Any]]:
    return {row["thread_id"]: row for row in json.loads(response.content)["threads"]}


async def test_rows_carry_each_threads_status_and_the_mounts_agent_id() -> None:
    run_store = DefaultRunStore()
    request = await _thread("t1", "t2")
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)

    rows = _rows(await _list(_server(run_store, id="helper")))

    assert rows["t1"]["status"] == "running"
    assert rows["t1"]["agent_id"] == "helper"
    assert rows["t1"]["last_read_at"] is None
    assert rows["t2"]["status"] == "idle"
    assert "interrupts" not in rows["t1"]
    assert "reason" not in rows["t1"]


async def test_a_waiting_row_serves_the_protocols_interrupt_list() -> None:
    run_store = DefaultRunStore()
    request = await _thread("t1")
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)
    waiting = RunWaiting(
        kind="approval", summary="Delete the invoice?", tool_call_id="call-9", destructive=True
    )
    await run_store.finish("t1", "r1", RunOutcome.WAITING, request=request, waiting=waiting)

    row = _rows(await _list(_server(run_store)))["t1"]

    assert row["status"] == "waiting"
    assert row["interrupts"] == [
        {
            "id": "int-call-9",
            "reason": "tool_call",
            "message": "Delete the invoice?",
            "toolCallId": "call-9",
            "metadata": {"destructive": True, "kind": "approval"},
        }
    ]
    assert "agent_id" not in row


@pytest.mark.parametrize(
    ("outcome", "status", "reason"),
    [(RunOutcome.ERROR, "failed", "error"), (RunOutcome.TIMED_OUT, "idle", None)],
)
async def test_a_reason_is_served_only_with_failed(
    outcome: RunOutcome, status: str, reason: str | None
) -> None:
    run_store = DefaultRunStore()
    request = await _thread("t1")
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)
    await run_store.finish("t1", "r1", outcome, request=request)

    row = _rows(await _list(_server(run_store)))["t1"]

    assert row["status"] == status
    assert row.get("reason") == reason


async def test_without_a_run_store_rows_keep_their_old_shape() -> None:
    await _thread("t1")

    body = json.loads((await _list(_server(id="helper"))).content)

    assert set(body["threads"][0]) == {"thread_id", "title", "updated_at", "preview"}
    assert body["agent"] == {"id": "helper", "name": None, "description": None}


async def test_the_mount_carries_its_name_and_description() -> None:
    await _thread("t1")
    server = _server(DefaultRunStore(), name="Helper", description="Answers billing questions")

    body = json.loads((await _list(server)).content)

    assert server.name == "Helper"
    assert server.description == "Answers billing questions"
    assert body["agent"] == {
        "id": None,
        "name": "Helper",
        "description": "Answers billing questions",
    }


async def test_a_mount_with_no_identity_adds_no_agent_key() -> None:
    await _thread("t1")

    body = json.loads((await _list(_server(DefaultRunStore()))).content)

    assert "agent" not in body


async def test_posting_read_marks_the_thread_read() -> None:
    run_store = DefaultRunStore()
    request = await _thread("t1")
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)
    server = _server(run_store)
    read = AuthedAsyncRequestFactory().post("/agent/threads/t1/read/")

    response = await _route(server, "thread_read")(read, thread_id="t1")

    assert response.status_code == 204
    assert _rows(await _list(server))["t1"]["last_read_at"] is not None


async def test_read_answers_404_for_a_thread_the_caller_does_not_own() -> None:
    run_store = DefaultRunStore()
    request = await _thread("t1")
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)
    server = _server(run_store)
    stranger = AsyncRequestFactory().post("/agent/threads/t1/read/")
    stranger.user = authenticated_user(pk=2, username="stranger")

    response = await _route(server, "thread_read")(stranger, thread_id="t1")

    assert response.status_code == 404
    state = await run_store.get("t1", request=request)
    assert state is not None and state.last_read_at is None


async def test_read_answers_only_a_post_and_refuses_an_anonymous_caller() -> None:
    server = _server(DefaultRunStore())
    view = _route(server, "thread_read")

    got = await view(AuthedAsyncRequestFactory().get("/agent/threads/t1/read/"), thread_id="t1")
    anonymous = await view(AsyncRequestFactory().post("/agent/threads/t1/read/"), thread_id="t1")

    assert got.status_code == 405
    assert anonymous.status_code in (401, 403)


def test_the_read_route_mounts_only_with_a_run_store() -> None:
    with_store, _, _ = _server(DefaultRunStore()).urls
    without, _, _ = _server().urls

    routes = {p.name: str(p.pattern) for p in with_store}
    assert routes["thread_read"] == "threads/<str:thread_id>/read/"
    assert "thread_read" not in {p.name for p in without}


async def test_a_poller_sending_the_etag_back_gets_304_until_a_row_changes() -> None:
    run_store = DefaultRunStore()
    request = await _thread("t1")
    server = _server(run_store)

    first = await _list(server)
    unchanged = await _list(server, **{"If-None-Match": first["ETag"]})
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)
    changed = await _list(server, **{"If-None-Match": first["ETag"]})

    assert first.status_code == 200
    assert first["Cache-Control"] == "private, no-cache"
    assert unchanged.status_code == 304
    assert unchanged["ETag"] == first["ETag"]
    assert changed.status_code == 200
    assert changed["ETag"] != first["ETag"]
    assert _rows(changed)["t1"]["status"] == "running"


async def _frames(stream: Any, count: int) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    buffer = ""
    async for chunk in stream:
        buffer += chunk if isinstance(chunk, str) else chunk.decode()
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
            if "data" in lines:
                frames.append((lines["id"], json.loads(lines["data"])))
            if len(frames) == count:
                return frames
    raise AssertionError("the stream ended early")


async def test_the_stream_sends_the_polled_body_again_on_each_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_view_module, "_STREAM_POLL_SECONDS", 0.01, raising=False)
    run_store = DefaultRunStore()
    request = await _thread("t1")
    server = _server(run_store)
    polled = await _list(server)

    response = await _list(server, **_SSE)
    assert isinstance(response, StreamingHttpResponse)
    assert response["Content-Type"] == "text/event-stream"
    stream = response.streaming_content
    first = await _frames(stream, 1)
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)
    second = await asyncio.wait_for(_frames(stream, 1), timeout=5)
    await stream.aclose()

    assert first == [(polled["ETag"], json.loads(polled.content))]
    assert second[0][1]["threads"][0]["status"] == "running"
    assert second[0][0] == (await _list(server))["ETag"]


async def test_a_stream_resumed_at_the_current_etag_skips_the_unchanged_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_view_module, "_STREAM_POLL_SECONDS", 0.01, raising=False)
    run_store = DefaultRunStore()
    request = await _thread("t1")
    server = _server(run_store)
    etag = (await _list(server))["ETag"]

    response = await _list(server, **_SSE, **{"Last-Event-ID": etag})
    stream = response.streaming_content
    pending = asyncio.ensure_future(_frames(stream, 1))
    await asyncio.sleep(0.1)
    assert not pending.done()
    await run_store.mark_read("t1", request=request)
    await run_store.claim("t1", "r1", request=request, lease=_LEASE)
    frames = await asyncio.wait_for(pending, timeout=5)
    await stream.aclose()

    assert frames[0][0] != etag
    assert frames[0][1]["threads"][0]["status"] == "running"


def test_a_run_store_without_a_conversation_store_mounts_no_read_route() -> None:
    server = AGUIServer(
        ToolRegistry(), model=TestModel(), csrf_exempt=True, run_store=DefaultRunStore()
    )

    patterns, _, _ = server.urls

    assert "thread_read" not in {p.name for p in patterns}


class _AnonymousStore:
    async def exists(self, thread_id: str, *, request: Any) -> bool:
        raise AnonymousOperationError("no owner")


async def test_read_answers_403_when_the_store_has_no_owner_for_the_caller() -> None:
    view = ThreadReadView(DefaultRunStore(), _AnonymousStore(), require_authenticated=False)

    response = await view(AsyncRequestFactory().post("/agent/threads/t1/read/"), thread_id="t1")

    assert response.status_code == 403


async def test_the_stream_keeps_its_heartbeat() -> None:
    await _thread("t1")
    server = AGUIServer(
        ToolRegistry(),
        model=TestModel(),
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        run_store=DefaultRunStore(),
        config=build_ag_ui_config(heartbeat_seconds=0.01),
    )

    response = await _list(server, **_SSE)
    stream = response.streaming_content
    chunks = [await anext(stream), await anext(stream)]
    await stream.aclose()

    assert chunks[0].startswith(b"id: ")
    assert chunks[1].startswith(b":")


async def test_reading_a_thread_no_run_claimed_answers_204_and_leaves_it_unread() -> None:
    await _thread("t1")
    server = _server(DefaultRunStore())
    read = AuthedAsyncRequestFactory().post("/agent/threads/t1/read/")

    response = await _route(server, "thread_read")(read, thread_id="t1")

    assert response.status_code == 204
    row = _rows(await _list(server))["t1"]
    assert (row["status"], row["last_read_at"]) == ("idle", None)
