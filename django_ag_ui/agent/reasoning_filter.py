from __future__ import annotations

from collections.abc import AsyncIterator

from ag_ui.core import BaseEvent, EventType

# Every reasoning event type. The protocol's 1.0 removed the older
# ``THINKING_*`` family, which pydantic-ai's adapter had already stopped
# emitting at the protocol versions this package supports, so ``REASONING_*``
# is the whole of it. Keyed off the enum member names so a reasoning event the
# protocol adds later is filtered without a change here.
REASONING_EVENT_TYPES: frozenset[EventType] = frozenset(
    event_type for event_type in EventType if event_type.name.startswith("REASONING")
)


async def drop_reasoning_events(stream: AsyncIterator[BaseEvent]) -> AsyncIterator[BaseEvent]:
    """Yield ``stream`` unchanged except for reasoning/thinking events, dropped.

    The privacy opt-out behind ``DJANGO_AG_UI["FORWARD_REASONING"] = False``: a
    consumer can enable a model's thinking budget (better answers) without
    streaming the model's chain-of-thought to the browser. Reasoning is a
    pure adapter pass-through, so suppressing it is a stream filter — no protocol
    change. Forwarding (the default) skips this filter entirely.
    """
    async for event in stream:
        if event.type not in REASONING_EVENT_TYPES:
            yield event


__all__ = ["REASONING_EVENT_TYPES", "drop_reasoning_events"]
