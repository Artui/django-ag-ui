from __future__ import annotations

from collections.abc import AsyncIterator

from ag_ui.core import BaseEvent, RunStartedEvent


async def stamp_parent_run_id(
    stream: AsyncIterator[BaseEvent], parent_run_id: str | None
) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, naming ``parent_run_id`` on a ``RUN_STARTED`` that names none."""
    async for event in stream:
        if (
            parent_run_id is not None
            and isinstance(event, RunStartedEvent)
            and event.parent_run_id is None
        ):
            event = event.model_copy(update={"parent_run_id": parent_run_id})
        yield event


__all__ = ["stamp_parent_run_id"]
