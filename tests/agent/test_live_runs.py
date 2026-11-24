from __future__ import annotations

import asyncio
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.types.run_outcome import RunOutcome

from django_ag_ui.agent.events_view import _UNFINISHED, EventsView
from django_ag_ui.agent.live_runs import LiveRuns
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_events_view import _DONE, _START, _TEXT, _claimed, _drain, _factory
from tests.agent.test_reattach import _SSE, _frames


def _view(store: DefaultRunStore, live: LiveRuns) -> EventsView:
    config = build_ag_ui_config(heartbeat_seconds=0)
    return EventsView(store, DefaultConversationStore(), config=config, live_runs=live)


async def _logged_now(store: DefaultRunStore, request: Any, *events: dict[str, Any]) -> list[Any]:
    last = await store.append("r1", list(events), request=request)
    assert last is not None
    return await store.read("r1", request=request, after=last - len(events))


def _following(view: EventsView) -> asyncio.Future[str]:
    request = _factory().get("/agent/threads/t1/events/?after=0", headers=_SSE)

    async def drained() -> str:
        return await _drain(await view(request, thread_id="t1"))

    return asyncio.ensure_future(drained())


@pytest.mark.django_db(transaction=True)
async def test_a_reattach_before_the_first_entry_follows_the_feed_from_it() -> None:
    store, live = DefaultRunStore(), LiveRuns()
    request = await _claimed(store)
    feed = live.open(request, "t1", "r1")
    assert feed is not None
    replay = _following(_view(store, live))
    await asyncio.sleep(0.05)

    feed.publish(await _logged_now(store, request, _START, _TEXT))
    feed.publish(await _logged_now(store, request, _DONE))

    types = [event["type"] for _, event in _frames(await replay)]
    assert types == ["RUN_STARTED", "MESSAGES_SNAPSHOT", "TEXT_MESSAGE_START", "RUN_FINISHED"]


@pytest.mark.django_db(transaction=True)
async def test_a_feed_that_closes_before_any_entry_ends_the_replay_from_the_store() -> None:
    store, live = DefaultRunStore(), LiveRuns()
    request = await _claimed(store)
    feed = live.open(request, "t1", "r1")
    assert feed is not None
    replay = _following(_view(store, live))
    await asyncio.sleep(0.05)

    await store.finish("t1", "r1", RunOutcome.CANCELLED, request=request)
    feed.close()

    events = [event for _, event in _frames(await replay)]
    assert events == [{"type": "RUN_ERROR", "message": _UNFINISHED, "code": "idle"}]


@pytest.mark.django_db(transaction=True)
async def test_a_feed_that_closes_with_no_terminal_event_tails_the_store_to_its_end() -> None:
    store, live = DefaultRunStore(), LiveRuns()
    request = await _claimed(store, _START)
    feed = live.open(request, "t1", "r1")
    assert feed is not None
    replay = _following(_view(store, live))
    await asyncio.sleep(0.05)

    feed.publish(await _logged_now(store, request, _TEXT))
    await store.finish("t1", "r1", RunOutcome.CANCELLED, request=request)
    feed.close()

    frames = _frames(await replay)
    assert [seq for seq, _ in frames if seq is not None] == [2]
    assert frames[-1][1] == {"type": "RUN_ERROR", "message": _UNFINISHED, "code": "idle"}


@pytest.mark.django_db(transaction=True)
async def test_a_feed_closed_before_the_stream_is_read_replays_from_the_store() -> None:
    store, live = DefaultRunStore(), LiveRuns()
    request = await _claimed(store, _START, _TEXT, _DONE)
    feed = live.open(request, "t1", "r1")
    assert feed is not None
    get = _factory().get("/agent/threads/t1/events/?after=0", headers=_SSE)
    response = await _view(store, live)(get, thread_id="t1")

    await store.finish("t1", "r1", RunOutcome.COMPLETED, request=request)
    feed.close()

    types = [event["type"] for _, event in _frames(await _drain(response))]
    assert types == ["RUN_STARTED", "MESSAGES_SNAPSHOT", "TEXT_MESSAGE_START", "RUN_FINISHED"]


async def test_an_anonymous_run_is_never_registered() -> None:
    live, request = LiveRuns(), AsyncRequestFactory().get("/")
    request.user = AnonymousUser()

    assert live.open(request, "t1", "r1") is None
    assert live.find(request, "t1", "r1") is None


def test_a_run_owned_on_another_loop_is_a_miss() -> None:
    live, request = LiveRuns(), _factory().get("/")

    async def opened() -> Any:
        return live.open(request, "t1", "r1")

    async def found() -> Any:
        return live.find(request, "t1", "r1")

    assert asyncio.run(opened()) is not None
    assert asyncio.run(found()) is None
