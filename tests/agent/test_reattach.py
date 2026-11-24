from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import timedelta
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.types.run_log_entry import RunLogEntry
from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent import events_view as events_view_module
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_events_view import (
    _START,
    _body,
    _claimed,
    _drain,
    _factory,
    _route,
    _server,
    _settled,
)
from tests.authed_request_factory import authenticated_user

_SSE = {"Accept": "text/event-stream"}
_FRAME = re.compile(r"(?:id: (\d+)\n)?data: (.*)\n\n")


class _Recording(DefaultRunStore):
    """``DefaultRunStore``, noting each log read and each wait it is asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[tuple[str, int]] = []
        self.waits: list[tuple[str, int]] = []

    async def read(
        self, run_id: str, *, request: HttpRequest, after: int = 0, limit: int | None = None
    ) -> list[RunLogEntry]:
        self.reads.append((run_id, after))
        return await super().read(run_id, request=request, after=after, limit=limit)

    async def wait(
        self, run_id: str, *, after: int, timeout: timedelta, request: HttpRequest
    ) -> bool:
        self.waits.append((run_id, after))
        return await super().wait(run_id, after=after, timeout=timeout, request=request)


class _Unwatchable:
    """A run store with ``RunStore``'s methods and no ``wait``, as one written before it."""

    def __init__(self) -> None:
        self._store = DefaultRunStore()
        self.reads = 0

    async def read(
        self, run_id: str, *, request: HttpRequest, after: int = 0, limit: int | None = None
    ) -> list[RunLogEntry]:
        self.reads += 1
        return await self._store.read(run_id, request=request, after=after, limit=limit)

    def __getattr__(self, name: str) -> Any:
        if name == "wait":
            raise AttributeError(name)
        return getattr(self._store, name)


class _Steps:
    """A model that thinks, then answers, each part after the test opens its gate."""

    def __init__(self, calls: int = 1) -> None:
        self.gates = [(asyncio.Event(), asyncio.Event()) for _ in range(calls)]
        self.said = [f"from call {i}" for i in range(calls)]
        self._calls = 0

    def model(self) -> FunctionModel:
        async def stream_fn(messages: list, info: Any) -> Any:
            call, self._calls = self._calls, self._calls + 1
            first, second = self.gates[call]
            yield {0: DeltaThinkingPart(content="thinking")}
            await first.wait()
            yield self.said[call]
            await second.wait()
            yield " and the rest"

        return FunctionModel(stream_function=stream_fn)

    def release(self, call: int = 0) -> None:
        for gate in self.gates[call]:
            gate.set()


class _HeldRead(DefaultRunStore):
    """``DefaultRunStore`` whose next full read is taken, then held until the test lets it go."""

    def __init__(self) -> None:
        super().__init__()
        self.hold: asyncio.Event | None = None
        self.taken = asyncio.Event()

    async def read(
        self, run_id: str, *, request: HttpRequest, after: int = 0, limit: int | None = None
    ) -> list[RunLogEntry]:
        read = await super().read(run_id, request=request, after=after, limit=limit)
        hold, self.hold = self.hold, None
        if hold is not None:
            self.taken.set()
            await hold.wait()
        return read


class _EagerWait(DefaultRunStore):
    """A store whose ``wait`` answers at once, as ``wait`` does for a run with no log."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    async def read(
        self, run_id: str, *, request: HttpRequest, after: int = 0, limit: int | None = None
    ) -> list[RunLogEntry]:
        self.reads += 1
        return await super().read(run_id, request=request, after=after, limit=limit)

    async def wait(
        self, run_id: str, *, after: int, timeout: timedelta, request: HttpRequest
    ) -> bool:
        return True


class _OtherUserFactory(AsyncRequestFactory):
    def request(self, **request: Any) -> Any:
        built = super().request(**request)
        built.user = authenticated_user(pk=2, username="other")
        return built


def _frames(sse: str) -> list[tuple[int | None, dict[str, Any]]]:
    return [(int(s) if s else None, json.loads(d)) for s, d in _FRAME.findall(sse)]


async def _start(server: Any, factory: Any = None, run_id: str = "r1") -> Any:
    factory = factory if factory is not None else _factory()
    request = factory.post("/agent/", data=_body(run_id), content_type="application/json")
    response = await _route(server, "endpoint")(request)
    stream = response.streaming_content
    async for chunk in stream:
        if "REASONING" in (chunk if isinstance(chunk, str) else chunk.decode()):
            return request, stream
    raise AssertionError("the run never thought")


async def _logged(store: Any, request: Any, at_least: int) -> int:
    for _ in range(300):
        log = await store.read("r1", request=request)
        if len(log) >= at_least:
            return len(log)
        await asyncio.sleep(0.01)
    raise AssertionError("the run never logged")


def _replay(server: Any, after: int, factory: Any = None) -> asyncio.Future[str]:
    factory = factory if factory is not None else _factory()
    request = factory.get(f"/agent/threads/t1/events/?after={after}", headers=_SSE)

    async def drained() -> str:
        return await _drain(await _route(server, "thread_events")(request, thread_id="t1"))

    return asyncio.ensure_future(drained())


async def _finish(steps: _Steps, stream: Any, call: int = 0) -> None:
    steps.release(call)
    async for _chunk in stream:
        pass


def test_the_recording_doubles_have_the_stores_shape() -> None:
    for double, name in [(d, n) for d in (_Recording, _EagerWait) for n in ("read", "wait")]:
        assert inspect.signature(getattr(double, name)) == inspect.signature(
            getattr(DefaultRunStore, name)
        )
    assert inspect.signature(_HeldRead.read) == inspect.signature(DefaultRunStore.read)
    assert inspect.signature(_Unwatchable.read) == inspect.signature(DefaultRunStore.read)
    assert not hasattr(_Unwatchable(), "wait")


@pytest.mark.django_db(transaction=True)
async def test_a_reattach_in_the_owning_process_reads_no_log_after_its_replay() -> None:
    steps, store = _Steps(), _Recording()
    server = _server(steps.model(), store)
    request, stream = await _start(server)
    await _logged(store, request, 2)
    store.reads.clear()

    replay = _replay(server, 0)
    await asyncio.sleep(0.05)
    await _finish(steps, stream)
    events = [event for _, event in _frames(await replay)]

    assert store.reads == [("r1", 0)]
    assert store.waits == []
    assert events[-1]["type"] == "RUN_FINISHED"
    assert "from call 0 and the rest" in "".join(e.get("delta", "") for e in events)
    await _settled(store, request)
    assert server._view.live_runs._feeds == {}


@pytest.mark.django_db(transaction=True)
async def test_every_event_arrives_once_across_the_replay_to_live_seam() -> None:
    steps, store = _Steps(), DefaultRunStore()
    server = _server(steps.model(), store)
    request, stream = await _start(server)
    replays: list[tuple[int, int, asyncio.Future[str]]] = []
    for gate in steps.gates[0]:
        logged = await _logged(store, request, 2)
        replays += [(after, logged, _replay(server, after)) for after in range(logged + 2)]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.sleep(0.05)
    async for _chunk in stream:
        pass
    log = {entry.seq: entry.event for entry in await store.read("r1", request=request)}

    for after, logged, replay in replays:
        tail = [(seq, event) for seq, event in _frames(await replay) if seq is not None]
        seqs = [seq for seq, _ in tail]
        if after <= logged:
            assert seqs[0] == max(after, 1) + 1, after
        assert seqs == list(range(seqs[0], max(log) + 1))
        assert all(event == log[seq] for seq, event in tail)


@pytest.mark.django_db(transaction=True)
async def test_a_reattach_never_follows_another_owners_run_of_the_same_id() -> None:
    steps, store = _Steps(calls=2), DefaultRunStore()
    server = _server(steps.model(), store)
    other = _OtherUserFactory()
    other_request, other_stream = await _start(server, other)
    _, mine = await _start(server)
    await _logged(store, other_request, 2)

    replay = _replay(server, 0, other)
    await asyncio.sleep(0.05)
    await _finish(steps, mine, call=1)
    await asyncio.sleep(0.05)
    await _finish(steps, other_stream, call=0)
    deltas = "".join(event.get("delta", "") for _, event in _frames(await replay))

    assert "from call 0 and the rest" in deltas
    assert "from call 1" not in deltas


@pytest.mark.django_db(transaction=True)
async def test_a_reattach_in_another_process_waits_on_the_store() -> None:
    steps, store = _Steps(), _Recording()
    owning = _server(steps.model(), store)
    serving = _server(steps.model(), store)
    request, stream = await _start(owning)
    await _logged(store, request, 2)
    store.reads.clear()

    replay = _replay(serving, 0)
    await asyncio.sleep(0.05)
    await _finish(steps, stream)
    events = [event for _, event in _frames(await replay)]

    assert store.waits
    assert events[-1]["type"] == "RUN_FINISHED"
    assert "from call 0 and the rest" in "".join(e.get("delta", "") for e in events)


@pytest.mark.django_db(transaction=True)
async def test_a_store_with_no_wait_is_polled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events_view_module, "_POLL_SECONDS", 0.01)
    steps, store = _Steps(), _Unwatchable()
    owning = _server(steps.model(), store)
    serving = _server(steps.model(), store)
    request, stream = await _start(owning)
    await _logged(store, request, 2)

    replay = _replay(serving, 0)
    await asyncio.sleep(0.05)
    await _finish(steps, stream)
    events = [event for _, event in _frames(await replay)]

    assert events[-1]["type"] == "RUN_FINISHED"
    assert "from call 0 and the rest" in "".join(e.get("delta", "") for e in events)


@pytest.mark.django_db(transaction=True)
async def test_with_live_reattach_off_the_owning_process_reads_the_store() -> None:
    steps, store = _Steps(), _Recording()
    config = build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300, live_reattach=False)
    server = _server(steps.model(), store, config=config)
    request, stream = await _start(server)
    await _logged(store, request, 2)
    store.reads.clear()

    replay = _replay(server, 0)
    await asyncio.sleep(0.05)
    await _finish(steps, stream)
    events = [event for _, event in _frames(await replay)]

    assert len(store.reads) > 1
    assert store.waits
    assert events[-1]["type"] == "RUN_FINISHED"


@pytest.mark.django_db(transaction=True)
async def test_a_reattach_to_a_finished_run_reads_the_store() -> None:
    steps, store = _Steps(), DefaultRunStore()
    server = _server(steps.model(), store)
    request, stream = await _start(server)
    await _finish(steps, stream)
    await _settled(store, request)

    events = [event for _, event in _frames(await _replay(server, 0))]

    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_FINISHED"


@pytest.mark.django_db(transaction=True)
async def test_entries_logged_while_the_replay_reads_are_followed_not_lost() -> None:
    steps, store = _Steps(), _HeldRead()
    server = _server(steps.model(), store)
    request, stream = await _start(server)
    await _logged(store, request, 2)
    hold = store.hold = asyncio.Event()

    replay = _replay(server, 0)
    await asyncio.wait_for(store.taken.wait(), timeout=5)
    steps.gates[0][0].set()
    await asyncio.sleep(0.1)
    hold.set()
    await _finish(steps, stream)
    seqs = [seq for seq, _ in _frames(await replay) if seq is not None]

    log = await DefaultRunStore().read("r1", request=request)
    assert seqs == list(range(2, log[-1].seq + 1))


@pytest.mark.django_db(transaction=True)
async def test_a_wake_that_answers_with_nothing_new_pauses_before_reading_again() -> None:
    store = _EagerWait()
    await _claimed(store, _START)
    serving = _server(TestModel(), store)
    store.reads = 0

    replay = _replay(serving, 0)
    await asyncio.sleep(0.5)
    replay.cancel()

    assert store.reads < 10


@pytest.mark.django_db(transaction=True)
async def test_a_wake_that_brought_entries_waits_again_rather_than_pausing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_view_module, "_POLL_SECONDS", 30)
    steps, store = _Steps(), _Recording()
    owning = _server(steps.model(), store)
    serving = _server(steps.model(), store)
    request, stream = await _start(owning)
    await _logged(store, request, 2)

    replay = _replay(serving, 0)
    first, second = steps.gates[0]
    await _waited(store, 1)
    first.set()
    rewaited = await _waited(store, 2)
    second.set()
    async for _chunk in stream:
        pass
    events = [event for _, event in _frames(await asyncio.wait_for(replay, timeout=5))]

    assert rewaited
    assert events[-1]["type"] == "RUN_FINISHED"


async def _waited(store: _Recording, times: int) -> bool:
    for _ in range(300):
        if len(store.waits) >= times:
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.django_db(transaction=True)
async def test_a_run_with_no_run_store_registers_no_feed() -> None:
    steps = _Steps()
    server = _server(steps.model())
    request = _factory().post("/agent/", data=_body("r1"), content_type="application/json")
    stream = (await _route(server, "endpoint")(request)).streaming_content
    async for chunk in stream:
        if "REASONING" in (chunk if isinstance(chunk, str) else chunk.decode()):
            break

    assert server._view.live_runs._feeds == {}
    await _finish(steps, stream)
