from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import StreamingHttpResponse
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent import events_view as events_view_module
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedAsyncRequestFactory


class _Gate:
    """A model that says something, then waits until the test lets it finish."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: Any) -> Any:
            yield "partial answer"
            await self.release.wait()
            yield " and the rest"

        return FunctionModel(stream_function=stream_fn)


def _server(model: Any, run_store: Any = None, **kwargs: Any) -> AGUIServer:
    kwargs.setdefault("config", build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300))
    return AGUIServer(
        ToolRegistry(),
        model=model,
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        run_store=run_store,
        **kwargs,
    )


def _route(server: AGUIServer, name: str) -> Any:
    patterns, _, _ = server.urls
    return next(p for p in patterns if p.name == name).callback


def _body(run_id: str, thread_id: str = "t1") -> bytes:
    return json.dumps(
        {
            "threadId": thread_id,
            "runId": run_id,
            "state": {},
            "messages": [{"id": f"u-{run_id}", "role": "user", "content": "hi"}],
            "tools": [],
            "context": [],
            "forwardedProps": {"detach": True},
        }
    ).encode()


_SSE = {"Accept": "text/event-stream"}


def _factory() -> AuthedAsyncRequestFactory:
    return AuthedAsyncRequestFactory()


async def _drain(response: StreamingHttpResponse) -> str:
    chunks = [c if isinstance(c, str) else c.decode() async for c in response]  # ty: ignore[not-iterable]
    return "".join(chunks)


def _events(sse: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in sse.splitlines() if line.startswith("data: ")]


async def _started(response: StreamingHttpResponse) -> Any:
    stream = response.streaming_content
    async for chunk in stream:  # ty: ignore[not-iterable]
        text = chunk if isinstance(chunk, str) else chunk.decode()
        if "partial answer" in text:
            return stream
    raise AssertionError("the run never spoke")


async def _settled(run_store: DefaultRunStore, request: Any) -> None:
    for _ in range(300):
        state = await run_store.get("t1", request=request)
        if state is not None and state.status is not ThreadRunStatus.RUNNING:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never settled")


def test_the_mount_serves_events_and_cancel_only_with_a_run_store() -> None:
    with_store = _server(TestModel(), DefaultRunStore())
    without = _server(TestModel())

    patterns, _, _ = with_store.urls
    routes = {p.name: str(p.pattern) for p in patterns}
    assert routes["thread_events"] == "threads/<str:thread_id>/events/"
    assert routes["thread_cancel"] == "threads/<str:thread_id>/cancel/"
    plain, _, _ = without.urls
    assert {"thread_events", "thread_cancel"}.isdisjoint({p.name for p in plain})


@pytest.mark.django_db(transaction=True)
async def test_a_finished_runs_replay_opens_with_run_started_and_ends_on_its_finish() -> None:
    run_store = DefaultRunStore()
    server = _server(TestModel(custom_output_text="hello there"), run_store)
    request = _factory().post("/agent/", data=_body("r1"), content_type="application/json")
    await _drain(await _route(server, "endpoint")(request))
    await _settled(run_store, request)

    replay = _factory().get("/agent/threads/t1/events/?after=0", headers=_SSE)
    response = await _route(server, "thread_events")(replay, thread_id="t1")
    events = _events(await _drain(response))

    types = [e["type"] for e in events]
    assert types[:2] == ["RUN_STARTED", "MESSAGES_SNAPSHOT"]
    assert types[-1] == "RUN_FINISHED"
    assert [m["id"] for m in events[1]["messages"]] == ["u-r1"]
    assert "hello there" in "".join(e.get("delta", "") for e in events)


@pytest.mark.django_db(transaction=True)
async def test_a_cancel_posted_mid_run_ends_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from django_ag_ui.agent import run_owner as run_owner_module

    monkeypatch.setattr(run_owner_module, "_WATCH_SECONDS", 0.01)
    gate, run_store = _Gate(), DefaultRunStore()
    server = _server(gate.model(), run_store)
    request = _factory().post("/agent/", data=_body("r1"), content_type="application/json")
    stream = await _started(await _route(server, "endpoint")(request))

    cancel = _route(server, "thread_cancel")(_factory().post("/agent/threads/t1/cancel/"), "t1")
    response = await cancel

    assert response.status_code == 202
    assert json.loads(response.content) == {"thread_id": "t1", "cancel_requested": True}
    async for _chunk in stream:
        pass
    await _settled(run_store, request)
    again = await _route(server, "thread_cancel")(_factory().post("/x/"), thread_id="t1")
    assert again.status_code == 404


_START = {"type": "RUN_STARTED", "threadId": "t1", "runId": "r1"}
_TEXT = {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"}
_DONE = {"type": "RUN_FINISHED", "threadId": "t1", "runId": "r1"}


async def _claimed(run_store: DefaultRunStore, *events: dict[str, Any]) -> Any:
    request = _factory().get("/")
    await run_store.claim("t1", "r1", request=request, lease=timedelta(minutes=5))
    if events:
        await run_store.append("r1", list(events), request=request)
    return request


async def _get(server: AGUIServer, query: str, **headers: str) -> Any:
    request = _factory().get(f"/agent/threads/t1/events/{query}", headers=headers)
    return await _route(server, "thread_events")(request, thread_id="t1")


@pytest.mark.django_db(transaction=True)
async def test_json_pages_carry_the_next_after_and_an_etag_a_poller_sends_back() -> None:
    run_store = DefaultRunStore()
    server = _server(TestModel(), run_store)
    request = await _claimed(run_store, _START, _TEXT)

    first = await _get(server, "?after=0&limit=1")
    second = await _get(server, "?after=1")
    await run_store.append("r1", [_DONE], request=request)
    await run_store.finish("t1", "r1", RunOutcome.COMPLETED, request=request)
    last = await _get(server, "?after=3")
    unchanged = await _get(server, "?after=3", **{"If-None-Match": last["ETag"]})

    page = json.loads(first.content)
    assert page == {
        "thread_id": "t1",
        "run_id": "r1",
        "status": "running",
        "events": [{"seq": 1, "event": _START}],
        "after": 1,
        "done": False,
    }
    assert [e["seq"] for e in json.loads(second.content)["events"]] == [2]
    assert json.loads(second.content)["done"] is False
    assert json.loads(last.content) | {"events": []} == {
        "thread_id": "t1",
        "run_id": "r1",
        "status": "idle",
        "events": [],
        "after": 3,
        "done": True,
    }
    assert last["ETag"] == '"r1:3:idle"'
    assert unchanged.status_code == 304
    assert unchanged["ETag"] == last["ETag"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("query", ["?after=-1", "?after=x", "?limit=0", "?limit=501"])
async def test_a_bad_after_or_limit_is_a_400(query: str) -> None:
    run_store = DefaultRunStore()
    server = _server(TestModel(), run_store)
    await _claimed(run_store, _START)

    response = await _get(server, query)

    assert response.status_code == 400


@pytest.mark.django_db(transaction=True)
async def test_a_thread_no_run_claimed_is_a_404_and_only_get_is_served() -> None:
    server = _server(TestModel(), DefaultRunStore())
    view = _route(server, "thread_events")

    missing = await _get(server, "")
    posted = await view(_factory().post("/agent/threads/t1/events/"), thread_id="t1")

    assert missing.status_code == 404
    assert json.loads(missing.content) == {"error": "the thread has no run", "thread_id": "t1"}
    assert posted.status_code == 405


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("route", "method"), [("thread_events", "get"), ("thread_cancel", "post")])
async def test_both_routes_refuse_an_anonymous_caller(route: str, method: str) -> None:
    gated = _route(_server(TestModel(), DefaultRunStore()), route)
    open_mount = _route(_server(TestModel(), DefaultRunStore(), require_authenticated=False), route)
    request = getattr(AsyncRequestFactory(), method)("/agent/threads/t1/x/")
    request.user = AnonymousUser()

    assert (await gated(request, thread_id="t1")).status_code == 401
    assert (await open_mount(request, thread_id="t1")).status_code == 403


@pytest.mark.django_db(transaction=True)
async def test_cancel_answers_only_a_post() -> None:
    cancel = _route(_server(TestModel(), DefaultRunStore()), "thread_cancel")

    response = await cancel(_factory().get("/agent/threads/t1/cancel/"), thread_id="t1")

    assert response.status_code == 405


@pytest.mark.django_db(transaction=True)
async def test_a_queued_runs_replay_waits_for_its_first_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_view_module, "_POLL_SECONDS", 0.01)
    run_store = DefaultRunStore()
    server = _server(TestModel(), run_store, config=build_ag_ui_config(heartbeat_seconds=0.005))
    request = await _claimed(run_store)
    replay = asyncio.ensure_future(_drain(await _get(server, "?after=0", **_SSE)))
    await asyncio.sleep(0.05)

    await run_store.append("r1", [_START, _TEXT], request=request)
    await asyncio.sleep(0.05)
    await run_store.append("r1", [_DONE], request=request)
    sse = await replay

    assert [e["type"] for e in _events(sse)] == [
        "RUN_STARTED",
        "MESSAGES_SNAPSHOT",
        "TEXT_MESSAGE_START",
        "RUN_FINISHED",
    ]
    assert ": django-ag-ui heartbeat" in sse
    assert "id: 2\ndata: " in sse


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("events", "outcome", "code"),
    [((), RunOutcome.CANCELLED, "idle"), ((_START, _TEXT), RunOutcome.ERROR, "error")],
)
async def test_a_run_that_ends_with_no_terminal_event_closes_on_a_run_error(
    monkeypatch: pytest.MonkeyPatch, events: tuple[Any, ...], outcome: RunOutcome, code: str
) -> None:
    monkeypatch.setattr(events_view_module, "_POLL_SECONDS", 0.01)
    run_store = DefaultRunStore()
    server = _server(TestModel(), run_store)
    request = await _claimed(run_store, *events)
    replay = asyncio.ensure_future(_drain(await _get(server, "?after=0", **_SSE)))
    await asyncio.sleep(0.05)

    await run_store.finish("t1", "r1", outcome, request=request)

    last = _events(await replay)[-1]
    assert last == {"type": "RUN_ERROR", "message": "the run ended without finishing", "code": code}
