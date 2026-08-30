"""Telling the page what moved, and the two routes onto the stream.

The agent writes and the page the user is looking at still shows the old list.
``ag-ui-run-finished`` already says *something* moved and every gallery frontend
already refetches on it -- so this is precision on a channel that ships, not a
new channel.

The choice being asserted here is which carrier. ``ACTIVITY_SNAPSHOT`` is
materialised into a message, persisted with the transcript and replayed on every
restore; ``CUSTOM`` is not. That is right for a chart and wrong for an
invalidation, because replaying one on every thread load is a refetch storm.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from ag_ui.core import BaseEvent, CustomEvent, EventType, TextMessageStartEvent

from django_ag_ui import (
    INVALIDATE_EVENT_NAME,
    INVALIDATION_SINK,
    publish_invalidation,
    resource_invalidation,
)
from django_ag_ui.agent.inject_invalidation_events import inject_invalidation_events


class TestTheEventItself:
    def test_it_is_a_custom_event_not_an_activity(self) -> None:
        # The whole reason the carrier was chosen: an activity is materialised
        # into a role="activity" message and replayed on every thread restore.
        event = resource_invalidation("orders")

        assert isinstance(event, CustomEvent)
        assert event.type == EventType.CUSTOM

    def test_it_carries_the_agreed_name(self) -> None:
        assert resource_invalidation("orders").name == INVALIDATE_EVENT_NAME

    def test_keys_ride_through_verbatim(self) -> None:
        # Opaque host-defined strings. This package never interprets them, which
        # is what keeps a Django app and a frontend that knows nothing about
        # Django models from having to share a vocabulary.
        event = resource_invalidation("board.events", "board.events/42", "anything at all")

        assert event.value["keys"] == ["board.events", "board.events/42", "anything at all"]

    def test_the_reason_rides_along_and_defaults_to_none(self) -> None:
        assert resource_invalidation("orders", reason="place_order").value["reason"] == (
            "place_order"
        )
        assert resource_invalidation("orders").value["reason"] is None

    def test_naming_nothing_is_allowed_and_says_nothing(self) -> None:
        # Not an error: a caller assembling keys from a loop that found none has
        # nothing to announce, and refusing would push a guard into every caller.
        assert resource_invalidation().value["keys"] == []

    def test_the_wire_shape_is_what_the_client_reads(self) -> None:
        # Read the payload actually served, by alias, rather than trusting the
        # Python attribute names to match the wire.
        dumped = resource_invalidation("orders", reason="place_order").model_dump(by_alias=True)

        assert dumped["type"] == "CUSTOM"
        assert dumped["name"] == "ag_ui.invalidate"
        assert dumped["value"] == {"keys": ["orders"], "reason": "place_order"}


async def _drain(stream: AsyncIterator[BaseEvent]) -> list[BaseEvent]:
    return [event async for event in stream]


def _text(message_id: str) -> TextMessageStartEvent:
    return TextMessageStartEvent(message_id=message_id)


class TestQueueingOntoTheStream:
    """``publish_invalidation`` -- the route that can wait for a commit."""

    async def test_a_queued_invalidation_reaches_the_stream(self) -> None:
        async def source() -> AsyncIterator[BaseEvent]:
            yield _text("m1")
            publish_invalidation("orders", reason="place_order")
            yield _text("m2")

        events = await _drain(inject_invalidation_events(source()))

        names = [getattr(event, "name", None) for event in events]
        assert INVALIDATE_EVENT_NAME in names

    async def test_it_arrives_during_the_run_not_at_the_end(self) -> None:
        # The point of queueing rather than returning: a long multi-step run
        # refreshes the list as its third write lands, not five minutes later.
        async def source() -> AsyncIterator[BaseEvent]:
            yield _text("m1")
            publish_invalidation("orders")
            yield _text("m2")
            yield _text("m3")

        events = await _drain(inject_invalidation_events(source()))

        kinds = [
            "invalidate" if getattr(event, "name", None) == INVALIDATE_EVENT_NAME else "text"
            for event in events
        ]
        assert kinds.index("invalidate") < kinds.index("text", kinds.index("invalidate"))
        assert kinds == ["text", "invalidate", "text", "text"]

    async def test_one_published_after_the_last_event_is_still_delivered(self) -> None:
        # The final drain. A write committed during the model request that
        # produces the closing events would otherwise be dropped.
        async def source() -> AsyncIterator[BaseEvent]:
            yield _text("m1")
            publish_invalidation("orders")

        events = await _drain(inject_invalidation_events(source()))

        assert getattr(events[-1], "name", None) == INVALIDATE_EVENT_NAME

    async def test_several_arrive_in_the_order_they_were_published(self) -> None:
        async def source() -> AsyncIterator[BaseEvent]:
            publish_invalidation("first")
            publish_invalidation("second")
            yield _text("m1")

        events = await _drain(inject_invalidation_events(source()))

        keys = [e.value["keys"][0] for e in events if getattr(e, "name", None) is not None]
        assert keys == ["first", "second"]

    async def test_a_stream_that_publishes_nothing_is_unchanged(self) -> None:
        async def source() -> AsyncIterator[BaseEvent]:
            yield _text("m1")
            yield _text("m2")

        events = await _drain(inject_invalidation_events(source()))

        assert len(events) == 2

    def test_publishing_outside_a_run_is_a_no_op_that_says_so(self) -> None:
        # A management command, a worker, a test. There is no stream to queue
        # onto, and raising would make every caller guard.
        assert publish_invalidation("orders") is False

    async def test_the_sink_does_not_leak_past_the_stream(self) -> None:
        async def source() -> AsyncIterator[BaseEvent]:
            yield _text("m1")

        await _drain(inject_invalidation_events(source()))

        assert publish_invalidation("orders") is False

    async def test_two_concurrent_runs_do_not_see_each_other(self) -> None:
        # The reason the sink is a ContextVar rather than state on anything: one
        # process serves many runs, and an invalidation crossing between them
        # would tell the wrong page to refetch.
        import asyncio

        async def run(key: str) -> list[Any]:
            async def source() -> AsyncIterator[BaseEvent]:
                yield _text("m1")
                publish_invalidation(key)
                await asyncio.sleep(0)
                yield _text("m2")

            events = await _drain(inject_invalidation_events(source()))
            return [e.value["keys"] for e in events if getattr(e, "name", None) is not None]

        first, second = await asyncio.gather(run("alpha"), run("beta"))

        assert first == [["alpha"]]
        assert second == [["beta"]]


def test_the_queue_is_a_delivery_mechanism_not_a_second_wire_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both routes put the identical event on the wire.

    The clock is frozen so the comparison can stay a *total* one. Two events
    built at different instants legitimately carry different timestamps, so
    without this the assertion would have to exclude that field -- and an
    exclusion is a hole exactly where a second wire format would hide. Freezing
    keeps the claim whole. The real clock is covered in ``test_event_timestamp``.
    """
    monkeypatch.setattr(
        "django_ag_ui.agent.resource_invalidation.event_timestamp", lambda: 1735689600000
    )
    direct = resource_invalidation("orders", reason="r").model_dump(by_alias=True)

    sink: list[CustomEvent] = []
    token = INVALIDATION_SINK.set(sink)
    try:
        publish_invalidation("orders", reason="r")
    finally:
        INVALIDATION_SINK.reset(token)

    assert sink[0].model_dump(by_alias=True) == direct
