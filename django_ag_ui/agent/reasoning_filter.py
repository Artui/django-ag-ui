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
    """Yield ``stream`` unchanged except for reasoning/thinking events, dropped."""
    async for event in stream:
        if event.type not in REASONING_EVENT_TYPES:
            yield event


__all__ = ["REASONING_EVENT_TYPES", "drop_reasoning_events"]
