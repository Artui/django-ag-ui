"""Tests for ``django_ag_ui.agent.event_timestamp``."""

from __future__ import annotations

import time

from ag_ui.core import BaseEvent, CustomEvent

from django_ag_ui.agent.event_timestamp import event_timestamp
from django_ag_ui.agent.resource_invalidation import resource_invalidation
from django_ag_ui.agent.subagent_lifecycle import subagent_lifecycle
from django_ag_ui.agent.subagent_progress import subagent_progress


def test_it_is_milliseconds_since_the_epoch() -> None:
    before = int(time.time() * 1000)
    stamped = event_timestamp()
    after = int(time.time() * 1000)

    assert before <= stamped <= after


def test_every_custom_event_this_package_builds_carries_one() -> None:
    """``CUSTOM`` used to be the only type in the stream without a wall clock.

    Both emitters, asserted together: stamping one and not the other would have
    traded a gap against the adapter for a gap inside this package.
    """
    events: list[CustomEvent] = [
        subagent_progress(
            delegation_id="call-1",
            agent="researcher",
            phase="tool_call",
            status="x",
            tool_call_id="call-2",
            tool_name="search",
        ),
        resource_invalidation("board.events", reason="schedule_event"),
    ]

    for event in events:
        assert isinstance(event.timestamp, int)


def test_every_event_this_package_builds_itself_carries_one() -> None:
    """The wider claim, now that this package builds three protocol events too.

    ``AGUIAdapter`` stamps what it emits; anything built here is outside that,
    so the delegation lifecycle has to stamp itself for the same reason the
    ``CUSTOM`` pair does. Asserted as one set rather than per builder, so a
    fourth event type added later fails here unless it is added below.
    """
    events: list[BaseEvent] = [
        subagent_lifecycle(delegation_id="call-1", agent="researcher", phase=phase)
        for phase in ("started", "finished", "failed")
    ]

    for event in events:
        assert isinstance(event.timestamp, int)
