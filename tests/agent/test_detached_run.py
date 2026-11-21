from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedAsyncRequestFactory


class _Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any) -> None:
        self.events.append(event)


class _Gate:
    """A model that says something, then waits until the test lets it finish."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = False

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: Any) -> Any:
            try:
                yield "partial answer"
                await self.release.wait()
                yield " and the rest"
            finally:
                self.closed = True

        return FunctionModel(stream_function=stream_fn)


def _body(run_id: str, *, detach: Any = None, thread_id: str = "t1") -> bytes:
    forwarded: dict[str, Any] = {} if detach is None else {"detach": detach}
    return json.dumps(
        {
            "threadId": thread_id,
            "runId": run_id,
            "state": {},
            "messages": [{"id": f"u-{run_id}", "role": "user", "content": "hi"}],
            "tools": [],
            "context": [],
            "forwardedProps": forwarded,
        }
    ).encode()


def _view(
    gate: _Gate, run_store: Any, audit: _Audit, timeout: float | None = 300
) -> DjangoAGUIView:
    return DjangoAGUIView(
        ToolRegistry(),
        model=gate.model(),
        csrf_exempt=True,
        audit_logger=audit,
        conversation_store=DefaultConversationStore(),
        run_store=run_store,
        config=build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=timeout),
    )


def _post(body: bytes) -> Any:
    return AuthedAsyncRequestFactory().post("/agent/", data=body, content_type="application/json")


async def _first_chunk(response: StreamingHttpResponse) -> Any:
    stream = response.streaming_content
    async for chunk in stream:  # ty: ignore[not-iterable]
        text = chunk if isinstance(chunk, str) else chunk.decode()
        if "partial answer" in text:
            return stream
    raise AssertionError("the run never spoke")


async def _settled(run_store: DefaultRunStore, request: Any) -> Any:
    for _ in range(200):
        state = await run_store.get("t1", request=request)
        if state is not None and state.status is not ThreadRunStatus.RUNNING:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("the run never settled")


async def _closed(gate: _Gate) -> None:
    for _ in range(200):
        if gate.closed:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run was never cancelled")


def _cancels(audit: _Audit) -> list[str]:
    return [e.error for e in audit.events if (e.error or "").startswith("cancelled")]


@pytest.mark.django_db(transaction=True)
async def test_a_second_start_on_a_running_thread_gets_a_409_naming_the_run() -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    view = _view(gate, run_store, audit)
    first = await view(_post(_body("r1")))
    stream = await _first_chunk(first)

    second = await view(_post(_body("r2")))

    assert second.status_code == 409
    assert json.loads(second.content) == {
        "error": "the thread has a run in progress",
        "thread_id": "t1",
        "run_id": "r1",
    }
    gate.release.set()
    async for _chunk in stream:
        pass


@pytest.mark.django_db(transaction=True)
async def test_a_detached_run_survives_its_clients_disconnect_with_no_cancel() -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    view = _view(gate, run_store, audit)
    request = _post(_body("r1", detach=True))
    stream = await _first_chunk(await view(request))

    await stream.aclose()
    gate.release.set()
    state = await _settled(run_store, request)

    assert state.status is ThreadRunStatus.IDLE
    assert state.reason is None
    assert _cancels(audit) == []
    conversation = await DefaultConversationStore().load("t1", request=request)
    assert "partial answer and the rest" in conversation.messages[-1]["content"]
    log = await run_store.read("r1", request=request)
    assert log[-1].event["type"] == "RUN_FINISHED"


@pytest.mark.django_db(transaction=True)
async def test_a_run_that_does_not_detach_still_cancels_on_disconnect() -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    view = _view(gate, run_store, audit)
    request = _post(_body("r1"))
    stream = await _first_chunk(await view(request))

    await stream.aclose()
    state = await _settled(run_store, request)

    assert gate.closed is True
    assert _cancels(audit) == ["cancelled: client disconnected mid-run"]
    assert state.status is ThreadRunStatus.IDLE
    assert state.active_run_id is None
    assert (await run_store.read("r1", request=request))[0].event["type"] == "RUN_STARTED"


@pytest.mark.django_db(transaction=True)
async def test_text_deltas_reach_the_log_batched() -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    gate.release.set()
    view = _view(gate, run_store, audit)
    request = _post(_body("r1"))
    async for _chunk in (await view(request)).streaming_content:  # ty: ignore[not-iterable]
        pass

    types = [entry.event["type"] for entry in await run_store.read("r1", request=request)]

    assert types.count("TEXT_MESSAGE_CONTENT") == 1
    contents = [
        entry.event["delta"]
        for entry in await run_store.read("r1", request=request)
        if entry.event["type"] == "TEXT_MESSAGE_CONTENT"
    ]
    assert contents == ["partial answer and the rest"]


@pytest.mark.django_db(transaction=True)
async def test_a_run_id_used_before_gets_a_409() -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    gate.release.set()
    view = _view(gate, run_store, audit)
    async for _chunk in (await view(_post(_body("r1")))).streaming_content:  # ty: ignore[not-iterable]
        pass

    again = await view(_post(_body("r1")))

    assert again.status_code == 409
    assert json.loads(again.content) == {"error": "that run id was used before", "run_id": "r1"}


async def test_an_anonymous_run_is_not_owned_through_the_run_store() -> None:
    gate, audit = _Gate(), _Audit()
    gate.release.set()
    view = DjangoAGUIView(
        ToolRegistry(),
        model=gate.model(),
        csrf_exempt=True,
        require_authenticated=False,
        audit_logger=audit,
        run_store=_RefusingStore(),
        config=build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300),
    )
    request = AsyncRequestFactory().post(
        "/agent/", data=_body("r1", detach=True), content_type="application/json"
    )

    response = await view(request)
    body = "".join(
        [c if isinstance(c, str) else c.decode() async for c in response.streaming_content]  # ty: ignore[not-iterable]
    )

    assert "RUN_FINISHED" in body


class _RefusingStore:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"an anonymous run reached the run store: {name}")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("run_id", "thread_id", "named"),
    [("r" * 300, "t1", "active_run_id"), ("r1", "t" * 300, "thread_id")],
)
async def test_an_over_long_id_on_a_run_store_mount_is_a_400_not_a_500(
    run_id: str, thread_id: str, named: str
) -> None:
    gate, audit = _Gate(), _Audit()
    view = DjangoAGUIView(
        ToolRegistry(),
        model=gate.model(),
        csrf_exempt=True,
        audit_logger=audit,
        run_store=DefaultRunStore(),
        config=build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300),
    )

    response = await view(_post(_body(run_id, thread_id=thread_id)))

    assert response.status_code == 400
    payload = json.loads(response.content)
    assert payload["code"] == "identifier_too_long"
    assert named in payload["error"]


@pytest.mark.django_db(transaction=True)
async def test_an_anonymous_run_with_an_over_long_id_never_asks_the_run_store() -> None:
    gate = _Gate()
    gate.release.set()
    view = DjangoAGUIView(
        ToolRegistry(),
        model=gate.model(),
        csrf_exempt=True,
        require_authenticated=False,
        run_store=_RefusingStore(),
        config=build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300),
    )
    request = AsyncRequestFactory().post(
        "/agent/", data=_body("r" * 300), content_type="application/json"
    )

    assert (await view(request)).status_code == 200


async def _disconnects_still_cancel(
    view: DjangoAGUIView, request: Any, gate: _Gate, audit: _Audit
) -> None:
    stream = await _first_chunk(await view(request))

    await stream.aclose()
    await _closed(gate)
    for _ in range(200):
        if _cancels(audit):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the cancel was never audited")


@pytest.mark.django_db(transaction=True)
async def test_an_anonymous_run_cannot_detach_and_cancels_on_disconnect() -> None:
    gate, audit = _Gate(), _Audit()
    view = DjangoAGUIView(
        ToolRegistry(),
        model=gate.model(),
        csrf_exempt=True,
        require_authenticated=False,
        audit_logger=audit,
        run_store=DefaultRunStore(),
        config=build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300),
    )
    request = AsyncRequestFactory().post(
        "/agent/", data=_body("r1", detach=True), content_type="application/json"
    )

    await _disconnects_still_cancel(view, request, gate, audit)

    assert _cancels(audit) == ["cancelled: client disconnected mid-run"]


@pytest.mark.django_db(transaction=True)
async def test_a_run_with_no_run_store_cannot_detach_and_cancels_on_disconnect() -> None:
    gate, audit = _Gate(), _Audit()
    view = _view(gate, None, audit)

    await _disconnects_still_cancel(view, _post(_body("r1", detach=True)), gate, audit)

    assert _cancels(audit) == ["cancelled: client disconnected mid-run"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("asked", ["true", 1])
async def test_only_a_json_true_detaches_so_a_truthy_value_still_cancels(asked: Any) -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    view = _view(gate, run_store, audit)
    request = _post(_body("r1", detach=asked))

    await _disconnects_still_cancel(view, request, gate, audit)
    await _settled(run_store, request)

    assert _cancels(audit) == ["cancelled: client disconnected mid-run"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("timeout", [None, 0])
async def test_a_mount_without_a_run_timeout_cannot_detach_and_cancels_on_disconnect(
    timeout: float | None,
) -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    view = _view(gate, run_store, audit, timeout)
    request = _post(_body("r1", detach=True))

    await _disconnects_still_cancel(view, request, gate, audit)
    await _settled(run_store, request)

    assert _cancels(audit) == ["cancelled: client disconnected mid-run"]


async def _unchanged(run_input: Any) -> tuple[Any, list[Any]]:
    return run_input, []


@pytest.mark.django_db(transaction=True)
async def test_a_headless_run_keeps_no_events_for_a_subscriber_it_never_has() -> None:
    gate, audit, run_store = _Gate(), _Audit(), DefaultRunStore()
    gate.release.set()
    view = _view(gate, run_store, audit)

    owner = await view.start_headless(
        _post(b""), AGUIAdapter.build_run_input(_body("r1")), timeout=30, prepare=_unchanged
    )

    assert await owner.wait() is not None
    assert owner._live is None or owner._live.empty()
