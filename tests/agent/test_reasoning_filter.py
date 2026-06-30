from __future__ import annotations

from ag_ui.core import (
    BaseEvent,
    EventType,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningStartEvent,
    TextMessageContentEvent,
    TextMessageStartEvent,
)

from django_ag_ui.agent.reasoning_filter import REASONING_EVENT_TYPES, drop_reasoning_events


def test_set_covers_both_event_families_but_not_text() -> None:
    # The adapter emits REASONING_* (>= 0.1.13) or THINKING_* (older); both must
    # be recognised, while ordinary text events must not be.
    assert EventType.REASONING_START in REASONING_EVENT_TYPES
    assert EventType.THINKING_START in REASONING_EVENT_TYPES
    assert EventType.TEXT_MESSAGE_CONTENT not in REASONING_EVENT_TYPES


async def test_drops_reasoning_events_keeps_the_rest() -> None:
    events: list[BaseEvent] = [
        TextMessageStartEvent(message_id="m1", role="assistant"),
        ReasoningStartEvent(message_id="r1"),
        ReasoningMessageContentEvent(message_id="r1", delta="thinking…"),
        TextMessageContentEvent(message_id="m1", delta="the answer"),
        ReasoningEndEvent(message_id="r1"),
    ]

    async def _stream():  # noqa: ANN202
        for event in events:
            yield event

    kept = [event async for event in drop_reasoning_events(_stream())]
    assert [event.type for event in kept] == [
        EventType.TEXT_MESSAGE_START,
        EventType.TEXT_MESSAGE_CONTENT,
    ]
