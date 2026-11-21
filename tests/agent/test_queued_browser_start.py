from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from collections.abc import Mapping
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import StreamingHttpResponse
from django_service_specs import NotPermitted
from django_spec_agents.child_sessions.types.inbound_kind import InboundKind
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.contrib.store.models import StoredRunEvent, StoredSnapshot
from django_spec_agents.headless.types.headless_firing import HeadlessFiring
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.policy.guard.types.tool_guard_config import ToolGuardConfig
from pydantic import ValidationError
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from django_ag_ui import ToolRegistry
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.agent.utils import is_permission_refusal
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.constants import QUEUED_INPUT_EVENT, QUEUED_RUN_LEASE
from tests.agent.test_park_run import (
    _AGAIN,
    _APPROVED,
    _DELETE_ARGS,
    _Calls,
    _guarded,
    _interleaved,
    _parked,
    _PlainRunStore,
)
from tests.agent.worker_queue import WorkerQueue
from tests.authed_request_factory import AuthedAsyncRequestFactory, authenticated_user

_CLIENT_TOOL = {
    "name": "pick_colour",
    "description": "Ask the user to pick a colour.",
    "parameters": {"type": "object", "properties": {}},
}


class _Model:
    """Answers, recording the tools and the last user text each request offers."""

    def __init__(self, *, call_client_tool: bool = False) -> None:
        self.offered: list[list[str]] = []
        self.prompts: list[str] = []
        self._call = call_client_tool

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            self.offered.append(sorted(t.name for t in [*info.function_tools, *info.output_tools]))
            self.prompts.append(_last_user_text(messages))
            if self._call:
                yield {0: DeltaToolCall(name="pick_colour", json_args="{}", tool_call_id="c1")}
                return
            yield "queued answer"

        return FunctionModel(stream_function=stream_fn)


def _last_user_text(messages: list) -> str:
    texts = [
        part.content
        for message in messages
        for part in getattr(message, "parts", [])
        if type(part).__name__ == "UserPromptPart" and isinstance(part.content, str)
    ]
    return texts[-1] if texts else ""


class _RefusingQueue(WorkerQueue):
    def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
        raise RuntimeError("the queue is down")


class _DeniedQueue(WorkerQueue):
    def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
        raise NotPermitted()


def _server(fake: _Model, queue: Any = None, **overrides: Any) -> AGUIServer:
    options: dict[str, Any] = {
        "model": fake.model(),
        "csrf_exempt": True,
        "conversation_store": DefaultConversationStore(),
        "step_store": DefaultStepStore,
        "run_store": DefaultRunStore(),
        "config": build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=120),
        "id": "browser",
        "firing_queue": queue,
        **overrides,
    }
    return AGUIServer(ToolRegistry(), **options)


def _queued(
    model: _Model, queue_class: type[WorkerQueue] = WorkerQueue
) -> tuple[AGUIServer, WorkerQueue]:
    class _Late:
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            return queue.enqueue(spec_name, params, actor=actor)

    server = _server(model, _Late())
    queue = queue_class(AGUIHeadlessRunner(server))
    return server, queue


def _body(run_id: str, *, detach: bool = True, **extra: Any) -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": run_id,
            "state": {"colour": "blue"},
            "messages": [{"id": f"u-{run_id}", "role": "user", "content": "paint it"}],
            "tools": [_CLIENT_TOOL],
            "context": [{"description": "page", "value": "settings"}],
            "forwardedProps": {"detach": detach},
            **extra,
        }
    ).encode()


def _route(server: AGUIServer, name: str) -> Any:
    patterns, _, _ = server.urls
    return next(p.callback for p in patterns if p.name == name)


def _post(body: bytes, path: str = "/agent/") -> Any:
    return AuthedAsyncRequestFactory().post(path, data=body, content_type="application/json")


async def _state(thread_id: str = "t1") -> Any:
    request = _post(b"{}")
    return await DefaultRunStore().get(thread_id, request=request)


async def _drain(response: StreamingHttpResponse) -> str:
    chunks = []
    async for chunk in response.streaming_content:  # ty: ignore[not-iterable]
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


def _events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")
    ]


@pytest.mark.django_db(transaction=True)
async def test_a_detached_start_answers_its_ids_and_enqueues_a_claimed_run() -> None:
    model = _Model()
    server, queue = _queued(model)
    tasks_before = len(asyncio.all_tasks())

    response = await _route(server, "endpoint")(_post(_body("r1")))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert json.loads(response.content) == {"thread_id": "t1", "run_id": "r1"}
    assert len(asyncio.all_tasks()) == tasks_before
    assert model.offered == []
    state = await _state()
    assert (state.status, state.active_run_id) == (ThreadRunStatus.RUNNING, "r1")
    [(spec_name, params, actor)] = queue.enqueued
    assert (spec_name, actor.pk) == ("run_headless", authenticated_user().pk)
    assert params == dataclasses.asdict(
        HeadlessFiring("browser", "", 120, "t1", None, None, "r1", None)
    )


@pytest.mark.django_db(transaction=True)
async def test_the_worker_runs_the_whole_browser_input() -> None:
    model = _Model()
    server, queue = _queued(model)
    await _route(server, "endpoint")(_post(_body("r1")))

    [result] = await queue.work()
    stop = result.service_result

    assert (stop.thread_id, stop.run_id, stop.outcome) == ("t1", "r1", RunOutcome.COMPLETED)
    assert model.offered == [["pick_colour"]]
    assert model.prompts == ["paint it"]
    state = await _state()
    assert (state.status, state.last_run_id) == (ThreadRunStatus.IDLE, "r1")
    stored = await DefaultConversationStore().load("t1", request=_post(b"{}"))
    assert stored is not None
    assert [m.get("content") for m in stored.messages][-2:] == ["paint it", "queued answer"]


@pytest.mark.django_db(transaction=True)
async def test_the_worker_rebuilds_the_run_from_the_input_as_received() -> None:
    model = _Model()
    server, queue = _queued(model)
    await _route(server, "endpoint")(_post(_body("r1")))
    request = _post(b"{}")

    [entry] = await DefaultRunStore().read("r1", request=request, limit=1)

    assert entry.event["type"] == "CUSTOM"
    assert entry.event["name"] == QUEUED_INPUT_EVENT
    assert entry.event["value"]["input"] == json.loads(_body("r1"))
    assert entry.event["value"]["resume_from"] is None
    assert entry.event["value"]["resume_parked"] is False


@pytest.mark.django_db(transaction=True)
async def test_a_queued_runs_replay_from_zero_never_shows_its_stored_input() -> None:
    model = _Model()
    server, queue = _queued(model)
    await _route(server, "endpoint")(_post(_body("r1")))
    await queue.work()

    replay = AuthedAsyncRequestFactory().get(
        "/agent/threads/t1/events/?after=0", headers={"Accept": "text/event-stream"}
    )
    events = _events(await _drain(await _route(server, "thread_events")(replay, thread_id="t1")))
    page = AuthedAsyncRequestFactory().get("/agent/threads/t1/events/?after=0")
    listed = json.loads((await _route(server, "thread_events")(page, thread_id="t1")).content)

    types = [e["type"] for e in events]
    assert types[:2] == ["RUN_STARTED", "MESSAGES_SNAPSHOT"]
    assert types.count("RUN_STARTED") == 1
    assert types[-1] == "RUN_FINISHED"
    assert "CUSTOM" not in types
    assert listed["events"][0]["event"]["type"] == "RUN_STARTED"
    assert listed["events"][0]["seq"] == 2
    assert all(e["event"]["type"] != "CUSTOM" for e in listed["events"])


@pytest.mark.django_db(transaction=True)
async def test_a_replay_waits_past_the_stored_input_for_the_runs_first_event() -> None:
    model = _Model()
    server, queue = _queued(model)
    await _route(server, "endpoint")(_post(_body("r1")))
    replay = AuthedAsyncRequestFactory().get(
        "/agent/threads/t1/events/?after=0", headers={"Accept": "text/event-stream"}
    )
    response = await _route(server, "thread_events")(replay, thread_id="t1")

    reading = asyncio.ensure_future(_drain(response))
    await asyncio.sleep(0.05)
    assert not reading.done()
    await queue.work()

    types = [e["type"] for e in _events(await reading)]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "CUSTOM" not in types


@pytest.mark.django_db(transaction=True)
async def test_a_queued_client_tool_call_parks_as_external() -> None:
    model = _Model(call_client_tool=True)
    server, queue = _queued(model)
    await _route(server, "endpoint")(_post(_body("r1")))

    [result] = await queue.work()
    stop = result.service_result

    assert stop.outcome is RunOutcome.WAITING
    assert [(w.kind, w.tool_call_id) for w in stop.waiting] == [("external", "c1")]


@pytest.mark.django_db(transaction=True)
async def test_a_connected_start_still_streams_on_a_queued_mount() -> None:
    model = _Model()
    server, queue = _queued(model)

    response = await _route(server, "endpoint")(_post(_body("r1", detach=False)))

    assert isinstance(response, StreamingHttpResponse)
    assert "queued answer" in await _drain(response)
    assert queue.enqueued == []


@pytest.mark.django_db(transaction=True)
async def test_a_busy_thread_refuses_a_queued_start_and_enqueues_nothing() -> None:
    model = _Model()
    server, queue = _queued(model)
    await _route(server, "endpoint")(_post(_body("r1")))

    busy = await _route(server, "endpoint")(_post(_body("r2")))
    reused = await _route(server, "endpoint")(_post(_body("r1", threadId="t2")))

    assert busy.status_code == 409
    assert json.loads(busy.content)["run_id"] == "r1"
    assert reused.status_code == 409
    assert json.loads(reused.content) == {"error": "that run id was used before", "run_id": "r1"}
    assert len(queue.enqueued) == 1


@pytest.mark.django_db(transaction=True)
async def test_an_unknown_resume_is_refused_before_anything_is_claimed() -> None:
    model = _Model()
    server, queue = _queued(model)

    response = await _route(server, "resume")(
        _post(_body("r1"), "/agent/resume/gone/"), resume_from="gone"
    )

    assert response.status_code == 404
    assert queue.enqueued == []
    assert await _state() is None


@pytest.mark.django_db(transaction=True)
async def test_a_refused_enqueue_gives_the_claim_back() -> None:
    model = _Model()
    server, _ = _queued(model, _RefusingQueue)

    with pytest.raises(RuntimeError, match="the queue is down"):
        await _route(server, "endpoint")(_post(_body("r1")))

    assert await _state() is None
    assert await DefaultRunStore().read("r1", request=_post(b"{}")) == []


@pytest.mark.django_db(transaction=True)
async def test_a_refused_enqueue_a_store_cannot_release_fails_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def kept(self: Any, thread_id: str, run_id: str, *, request: Any) -> bool:
        return False

    monkeypatch.setattr(DefaultRunStore, "release", kept)
    server, _ = _queued(_Model(), _RefusingQueue)

    with pytest.raises(RuntimeError, match="the queue is down"):
        await _route(server, "endpoint")(_post(_body("r1")))

    state = await _state()
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)


@pytest.mark.django_db(transaction=True)
async def test_a_refused_enqueue_on_a_store_that_is_not_resumable_fails_the_claim() -> None:
    model = _Model()
    server = _server(model, _RefusingQueue.__new__(_RefusingQueue), run_store=_PlainRunStore())

    with pytest.raises(RuntimeError, match="the queue is down"):
        await _route(server, "endpoint")(_post(_body("r1")))

    state = await _state()
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)


@pytest.mark.django_db(transaction=True)
async def test_a_specs_permission_refusal_at_enqueue_answers_403_and_gives_the_claim_back() -> None:
    server, queue = _queued(_Model(), _DeniedQueue)

    response = await _route(server, "endpoint")(_post(_body("r1")))

    assert response.status_code == 403
    assert json.loads(response.content) == {"error": "the spec refuses that run"}
    assert queue.enqueued == []
    assert await _state() is None


@pytest.mark.django_db(transaction=True)
async def test_a_django_permission_denied_at_enqueue_answers_403() -> None:
    class _Denied(WorkerQueue):
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            raise PermissionDenied

    server, _ = _queued(_Model(), _Denied)

    response = await _route(server, "endpoint")(_post(_body("r1")))

    assert response.status_code == 403


def test_a_refusal_is_not_recognised_without_the_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "django_service_specs.authorization.not_permitted", None)

    assert is_permission_refusal(PermissionDenied())
    assert not is_permission_refusal(NotPermitted())
    assert not is_permission_refusal(RuntimeError())


@pytest.mark.django_db(transaction=True)
async def test_a_firing_whose_input_is_gone_fails_rather_than_runs_an_empty_prompt() -> None:
    model = _Model()
    server, queue = _queued(model)
    runner = AGUIHeadlessRunner(server)
    request = server.headless_request(authenticated_user())
    await DefaultRunStore().claim("t1", "r1", request=request, lease=QUEUED_RUN_LEASE)

    with pytest.raises(LookupError, match="r1"):
        await runner.run_to_first_stop(
            "browser", "", authenticated_user(), timeout=5, thread_id="t1", run_id="r1"
        )

    assert model.offered == []
    state = await _state()
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)


async def _queued_start_that_cannot_begin(model: _Model) -> tuple[AGUIServer, WorkerQueue]:
    server, queue = _queued(model)
    await _route(server, "endpoint")(_post(_body("r1")))
    return server, queue


async def _failed_claim(model: _Model) -> None:
    assert model.offered == []
    state = await _state()
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)


@pytest.mark.django_db(transaction=True)
async def test_a_run_whose_first_entry_is_not_the_kept_input_has_none() -> None:
    server, _ = _queued(_Model())
    request = _post(b"{}")
    await DefaultRunStore().claim("t1", "r1", request=request, lease=QUEUED_RUN_LEASE)
    other = {"type": "CUSTOM", "name": "something-else", "value": {}}
    await DefaultRunStore().append("r1", [other], request=request)

    assert await server.queued_input(request, "r1") is None


@pytest.mark.django_db(transaction=True)
async def test_a_kept_input_that_no_longer_parses_fails_the_claim_not_holds_it() -> None:
    model = _Model()
    server, queue = await _queued_start_that_cannot_begin(model)
    entry = await StoredRunEvent.objects.aget(log__run_id="r1", seq=1)
    entry.event["value"]["input"] = {"not": "a run input"}
    await entry.asave()

    with pytest.raises(ValidationError):
        await queue.work()

    await _failed_claim(model)


@pytest.mark.django_db(transaction=True)
async def test_a_worker_whose_wake_mark_cannot_be_read_fails_the_claim_not_holds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _Model()
    server, queue = await _queued_start_that_cannot_begin(model)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the inbox is down")

    monkeypatch.setattr(server, "inbox_mark", broken)

    with pytest.raises(RuntimeError, match="the inbox is down"):
        await queue.work()

    await _failed_claim(model)


@pytest.mark.django_db(transaction=True)
async def test_a_worker_whose_kept_input_cannot_be_read_fails_the_claim_not_holds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _Model()
    server, queue = await _queued_start_that_cannot_begin(model)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the log is down")

    monkeypatch.setattr(server, "queued_input", broken)

    with pytest.raises(RuntimeError, match="the log is down"):
        await queue.work()

    await _failed_claim(model)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"id": None}, "id="),
        ({"run_store": None}, "run_store="),
        ({"config": build_ag_ui_config(run_timeout_seconds=None)}, "run_timeout_seconds"),
    ],
)
def test_a_firing_queue_needs_a_mount_a_worker_can_run(
    overrides: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ImproperlyConfigured, match=reason):
        _server(_Model(), WorkerQueue.__new__(WorkerQueue), **overrides)


@pytest.mark.django_db(transaction=True)
async def test_an_approval_resumes_its_parked_run_on_the_worker() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls, run_timeout_seconds=120)
    registry = options.pop("registry")

    class _Late:
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            return queue.enqueue(spec_name, params, actor=actor)

    server = AGUIServer(registry, id="helper", firing_queue=_Late(), **options)
    queue = WorkerQueue(AGUIHeadlessRunner(server))
    stop, parked = await _parked(server)
    body = {
        "threadId": stop.thread_id,
        "runId": "r-answer",
        "messages": [],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": {"detach": True},
        "resume": _APPROVED,
    }
    request = _post(json.dumps(body).encode(), f"/agent/resume/{stop.run_id}/")

    response = await _route(server, "resume")(request, resume_from=stop.run_id)
    assert calls.ran == []
    [result] = await queue.work()

    assert parked.status is ThreadRunStatus.WAITING
    assert json.loads(response.content) == {"thread_id": stop.thread_id, "run_id": "r-answer"}
    assert result.service_result.outcome is RunOutcome.COMPLETED
    assert calls.ran == ["widget-1"]
    [entry] = await DefaultRunStore().read("r-answer", request=request, limit=1)
    assert entry.event["value"]["resume_from"] == stop.run_id
    assert entry.event["value"]["resume_parked"] is True


@pytest.mark.django_db(transaction=True)
async def test_a_parked_approval_resumed_on_another_thread_is_not_queued() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls, run_timeout_seconds=120)
    registry = options.pop("registry")

    class _Late:
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            return queue.enqueue(spec_name, params, actor=actor)

    server = AGUIServer(registry, id="helper", firing_queue=_Late(), **options)
    queue = WorkerQueue(AGUIHeadlessRunner(server))
    stop, _ = await _parked(server)
    body = json.loads(_body("r-other"))
    body.update(threadId="t-other", messages=[], tools=[], resume=_APPROVED)
    request = _post(json.dumps(body).encode(), f"/agent/resume/{stop.run_id}/")

    response = await _route(server, "resume")(request, resume_from=stop.run_id)

    assert response.status_code == 404
    assert queue.enqueued == []
    assert calls.ran == []
    assert await _state("t-other") is None


@pytest.mark.django_db(transaction=True)
async def test_a_resume_whose_snapshot_is_gone_by_the_worker_fails_its_claim() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    options = _guarded(calls, run_timeout_seconds=120)
    registry = options.pop("registry")

    class _Late:
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            return queue.enqueue(spec_name, params, actor=actor)

    server = AGUIServer(registry, id="helper", firing_queue=_Late(), **options)
    queue = WorkerQueue(AGUIHeadlessRunner(server))
    stop, _ = await _parked(server)
    body = json.loads(_body("r-answer"))
    body.update(threadId=stop.thread_id, messages=[], tools=[], resume=_APPROVED)
    request = _post(json.dumps(body).encode(), f"/agent/resume/{stop.run_id}/")
    await _route(server, "resume")(request, resume_from=stop.run_id)
    await StoredSnapshot.objects.all().adelete()

    with pytest.raises(LookupError, match="r-answer"):
        await queue.work()

    assert calls.ran == []
    state = await _state(stop.thread_id)
    assert (state.status, state.active_run_id) == (ThreadRunStatus.FAILED, None)


@pytest.mark.django_db(transaction=True)
async def test_mail_that_lands_during_a_queued_browser_run_wakes_its_thread() -> None:
    inbox = DefaultInboundQueue()
    user = authenticated_user()
    sent: list[int] = []
    holder: dict[str, AGUIServer] = {}

    async def stream_fn(messages: list, info: AgentInfo) -> Any:
        sent.append(len(messages))
        if len(sent) == 1:
            request = holder["server"].headless_request(user)
            await inbox.enqueue(
                ThreadRef("t1", "browser"), InboundKind.STEER, {"text": "hi"}, request=request
            )
        yield "answer"

    class _Late:
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            return queue.enqueue(spec_name, params, actor=actor)

    model = FunctionModel(stream_function=stream_fn)
    server = holder["server"] = _server(_Model(), _Late(), model=model, inbound_queue=inbox)
    queue = WorkerQueue(AGUIHeadlessRunner(server))
    await _route(server, "endpoint")(_post(_body("r1")))

    await queue.work()
    for _ in range(300):
        if len(sent) == 2:
            break
        await asyncio.sleep(0.01)

    assert len(sent) == 2


async def _parked_on_queue(
    calls: _Calls, queue_class: type[WorkerQueue] = WorkerQueue, **config: Any
) -> tuple[AGUIServer, WorkerQueue, Any]:
    options = _guarded(calls, run_timeout_seconds=120, **config)
    registry = options.pop("registry")

    class _Late:
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            return queue.enqueue(spec_name, params, actor=actor)

    server = AGUIServer(registry, id="helper", firing_queue=_Late(), **options)
    queue = queue_class(AGUIHeadlessRunner(server))
    stop, _ = await _parked(server)
    return server, queue, stop


async def _resume_on(
    server: AGUIServer,
    stop: Any,
    run_id: str,
    *,
    detach: bool = True,
    resume: Any = _APPROVED,
    messages: Any = (),
) -> Any:
    body = json.loads(_body(run_id, detach=detach))
    body.update(threadId=stop.thread_id, messages=list(messages), tools=[], resume=resume)
    request = _post(json.dumps(body).encode(), f"/agent/resume/{stop.run_id}/")
    response = await _route(server, "resume")(request, resume_from=stop.run_id)
    if isinstance(response, StreamingHttpResponse):
        return await _drain(response)
    return response


@pytest.mark.django_db(transaction=True)
async def test_two_interleaved_queued_resumes_of_one_park_run_its_approved_call_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    server, queue, stop = await _parked_on_queue(calls)

    async def inner(store: Any, thread_id: str, request: Any) -> None:
        await _resume_on(server, stop, "r-inner")
        await queue.work()

    _interleaved(monkeypatch, "r-outer", inner)
    response = await _resume_on(server, stop, "r-outer")
    await queue.work()

    assert calls.ran == ["widget-1"]
    assert response.status_code == 404
    assert len(queue.enqueued) == 1


@pytest.mark.django_db(transaction=True)
async def test_a_queued_resume_that_loses_its_park_to_a_live_one_names_the_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    server, queue, stop = await _parked_on_queue(calls)

    async def inner(store: Any, thread_id: str, request: Any) -> None:
        await _resume_on(server, stop, "r-inner")

    _interleaved(monkeypatch, "r-outer", inner)
    response = await _resume_on(server, stop, "r-outer")

    assert response.status_code == 409
    assert json.loads(response.content)["run_id"] == "r-inner"
    assert len(queue.enqueued) == 1


@pytest.mark.django_db(transaction=True)
async def test_a_refused_resume_keeps_its_park_for_a_later_resume() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    server, queue, stop = await _parked_on_queue(calls, _DeniedQueue)

    refused = await _resume_on(server, stop, "r-refused")
    state = await _state(stop.thread_id)
    streamed = await _resume_on(server, stop, "r-answer", detach=False)

    assert refused.status_code == 403
    assert (state.status, state.last_run_id) == (ThreadRunStatus.WAITING, stop.run_id)
    assert state.waiting
    assert "deleted widget-1" in streamed
    assert calls.ran == ["widget-1"]
    assert await DefaultRunStore().read("r-refused", request=_post(b"{}")) == []


@pytest.mark.django_db(transaction=True)
async def test_mail_that_lands_before_the_worker_takes_a_browser_start_up_wakes_it_once() -> None:
    inbox = DefaultInboundQueue()
    sent: list[int] = []

    async def stream_fn(messages: list, info: AgentInfo) -> Any:
        sent.append(len(messages))
        yield "answer"

    class _Late:
        def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
            return queue.enqueue(spec_name, params, actor=actor)

    model = FunctionModel(stream_function=stream_fn)
    server = _server(_Model(), _Late(), model=model, inbound_queue=inbox)
    queue = WorkerQueue(AGUIHeadlessRunner(server))
    await _route(server, "endpoint")(_post(_body("r1")))
    request = server.headless_request(authenticated_user())
    await inbox.enqueue(ThreadRef("t1", "browser"), InboundKind.STEER, {"x": 1}, request=request)

    await queue.work()
    await asyncio.sleep(0.1)

    assert len(sent) == 2
    [(_, params, _)] = queue.enqueued
    assert params["inbox_mark"] == 0


@pytest.mark.django_db(transaction=True)
async def test_a_queued_resume_of_a_run_that_did_not_park_is_no_park_resume() -> None:
    calls = _Calls("delete_thing", _DELETE_ARGS)
    unguarded = ToolGuardConfig(enabled=False)
    server, queue, stop = await _parked_on_queue(calls, tool_guard=unguarded)

    response = await _resume_on(server, stop, "r-next", resume=None, messages=_AGAIN)
    [result] = await queue.work()

    assert response.status_code == 200
    assert result.service_result.outcome is RunOutcome.COMPLETED
