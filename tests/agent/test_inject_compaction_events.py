"""``inject_compaction_events`` — putting recorded compactions on the wire."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import BaseEvent, EventType, TextMessageStartEvent
from opentelemetry.trace import NoOpTracer
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import SlidingWindow

from django_ag_ui.agent.compaction_observer import COMPACTION_SINK, Compaction, CompactionObserver
from django_ag_ui.agent.inject_compaction_events import (
    COMPACTION_ACTIVITY_TYPE,
    inject_compaction_events,
)

_MODEL = TestModel()


class _Ctx:
    tracer = NoOpTracer()
    model = _MODEL


def _request_context(messages: list[Any]) -> ModelRequestContext:
    """The genuine upstream context — see ``test_compaction_observer`` for why."""
    return ModelRequestContext(
        model=_MODEL,
        messages=messages,
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )


def _text(message_id: str) -> BaseEvent:
    return TextMessageStartEvent(message_id=message_id)


async def _record(compaction: Compaction) -> None:
    sink = COMPACTION_SINK.get()
    assert sink is not None
    sink.append(compaction)


async def _collect(stream: AsyncIterator[BaseEvent]) -> list[BaseEvent]:
    return [event async for event in stream]


async def test_a_stream_with_no_compaction_is_unchanged() -> None:
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        yield _text("b")

    out = await _collect(inject_compaction_events(upstream()))
    assert [event.type for event in out] == [EventType.TEXT_MESSAGE_START] * 2


async def test_a_compaction_is_injected_before_the_next_event() -> None:
    # Ordering is the point: the notice belongs immediately above the turn that
    # ran with the shortened history, not at the end of the run.
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        await _record(Compaction(before=10, after=2))
        yield _text("b")

    out = await _collect(inject_compaction_events(upstream()))
    assert [event.type for event in out] == [
        EventType.TEXT_MESSAGE_START,
        EventType.ACTIVITY_SNAPSHOT,
        EventType.TEXT_MESSAGE_START,
    ]


async def test_the_injected_event_carries_the_sizes() -> None:
    async def upstream() -> AsyncIterator[BaseEvent]:
        await _record(Compaction(before=10, after=2))
        yield _text("a")

    out = await _collect(inject_compaction_events(upstream()))
    activity = out[0]
    assert activity.activity_type == COMPACTION_ACTIVITY_TYPE
    assert activity.content == {"removed": 8, "before": 10, "after": 2}
    assert activity.message_id.startswith("compaction-")


async def test_a_compaction_recorded_during_the_last_event_still_ships() -> None:
    # Without the drain after the loop this one is dropped on the floor — the
    # last model request of a run is exactly when a history is longest.
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        await _record(Compaction(before=6, after=3))

    out = await _collect(inject_compaction_events(upstream()))
    assert [event.type for event in out] == [
        EventType.TEXT_MESSAGE_START,
        EventType.ACTIVITY_SNAPSHOT,
    ]


async def test_several_compactions_each_get_their_own_event() -> None:
    async def upstream() -> AsyncIterator[BaseEvent]:
        await _record(Compaction(before=10, after=5))
        await _record(Compaction(before=8, after=4))
        yield _text("a")

    out = await _collect(inject_compaction_events(upstream()))
    activities = [event for event in out if event.type == EventType.ACTIVITY_SNAPSHOT]
    assert len(activities) == 2
    # Distinct occurrences, not a mutation of one activity message.
    assert activities[0].message_id != activities[1].message_id


async def test_the_sink_is_reset_when_the_stream_ends() -> None:
    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")

    await _collect(inject_compaction_events(upstream()))
    assert COMPACTION_SINK.get() is None


async def test_the_sink_is_reset_when_the_stream_raises() -> None:
    class _Boom(Exception): ...

    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        raise _Boom

    with contextlib.suppress(_Boom):
        await _collect(inject_compaction_events(upstream()))
    # A run that errors mid-stream must not leave the sink bound; the next run
    # on this task would otherwise write into a dead list.
    assert COMPACTION_SINK.get() is None


async def test_end_to_end_with_a_real_compaction_capability() -> None:
    observer = CompactionObserver(SlidingWindow(max_messages=4, keep_messages=2))

    async def upstream() -> AsyncIterator[BaseEvent]:
        yield _text("a")
        await observer.before_model_request(_Ctx(), _request_context(["m"] * 10))
        yield _text("b")

    out = await _collect(inject_compaction_events(upstream()))
    activities = [event for event in out if event.type == EventType.ACTIVITY_SNAPSHOT]
    assert len(activities) == 1
    assert activities[0].content == {"removed": 8, "before": 10, "after": 2}
