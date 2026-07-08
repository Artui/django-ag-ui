from __future__ import annotations

from collections.abc import AsyncIterator

from ag_ui.core import BaseEvent, EventType

# Every reasoning / thinking event type, across AG-UI protocol versions: the
# adapter emits the ``THINKING_*`` family below 0.1.13 and the ``REASONING_*``
# family at/above it (see pydantic-ai's version-gated thinking handlers). Keyed
# off the enum member names so the set stays correct as the protocol evolves.
REASONING_EVENT_TYPES: frozenset[EventType] = frozenset(
    event_type for event_type in EventType if event_type.name.startswith(("THINKING", "REASONING"))
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
