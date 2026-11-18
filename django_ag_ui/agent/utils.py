from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from ag_ui.core import Message, ToolMessage
from django_spec_agents import IdentifierTooLongError

from django_ag_ui.agent.stamp_answered_by import ANSWERED_BY_FIELD
from django_ag_ui.constants import QUEUED_INPUT_EVENT

ERROR_CODE = "error"
TIMED_OUT_CODE = "timed_out"
# A detached run whose lease lapsed; no run this package serves ends that way yet.
INTERRUPTED_CODE = "interrupted"
IDENTIFIER_TOO_LONG_CODE = "identifier_too_long"
RUN_ERROR_CODES: frozenset[str] = frozenset(
    {ERROR_CODE, TIMED_OUT_CODE, INTERRUPTED_CODE, IDENTIFIER_TOO_LONG_CODE}
)

STREAM_END = object()
"""Queued by the pump to say the upstream stream is exhausted.

A sentinel rather than letting ``StopAsyncIteration`` out of the pump: raised
inside a task it is not the loop-ending signal it is inside an ``async for``,
and asyncio turns it into a bare ``RuntimeError`` on the way through.
"""


async def pump_in_lockstep(stream: AsyncIterator[Any], out: asyncio.Queue[Any]) -> None:
    """Move ``stream`` into ``out`` one item at a time, then say how it ended."""
    try:
        async for item in stream:
            await out.put(item)
            await out.join()
        await out.put(STREAM_END)
    except Exception as error:
        await out.put(error)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()


def run_error_code(error: Exception) -> str:
    """The ``RUN_ERROR_CODES`` member a run that raised ``error`` ends with."""
    if isinstance(error, IdentifierTooLongError):
        return IDENTIFIER_TOO_LONG_CODE
    if isinstance(error, TimeoutError):
        return TIMED_OUT_CODE
    return ERROR_CODE


async def discard_tasks(*tasks: asyncio.Future[Any] | None) -> None:
    """Cancel whichever of ``tasks`` are still live, and wait for them to settle."""
    live = [task for task in tasks if task is not None]
    for task in live:
        task.cancel()
    await asyncio.gather(*live, return_exceptions=True)


def a2ui_validator() -> Callable[..., dict[str, Any]]:
    """The toolkit's component validator, or an error naming the ``[a2ui]`` extra."""
    try:
        from ag_ui_a2ui_toolkit.validate import validate_a2ui_components
    except ImportError as error:
        raise ImportError(
            "DJANGO_AG_UI['A2UI'] requires ag-ui-a2ui-toolkit: install django-ag-ui[a2ui]"
        ) from error
    return validate_a2ui_components


def without_answered_by(message: Message) -> Message:
    """``message`` without a client's claim of who answered a call; only the server marks."""
    if not isinstance(message, ToolMessage):
        return message
    metadata = dict(message.metadata or {})
    if metadata.pop(ANSWERED_BY_FIELD, None) is None:
        return message
    return message.model_copy(update={"metadata": metadata or None})


def is_queued_input(event: Any) -> bool:
    """Whether a run log event is a queued browser start's kept input, never sent to a client."""
    return (
        isinstance(event, dict)
        and event.get("type") == "CUSTOM"
        and event.get("name") == QUEUED_INPUT_EVENT
    )


__all__ = [
    "is_queued_input",
    "ERROR_CODE",
    "IDENTIFIER_TOO_LONG_CODE",
    "INTERRUPTED_CODE",
    "RUN_ERROR_CODES",
    "STREAM_END",
    "TIMED_OUT_CODE",
    "a2ui_validator",
    "discard_tasks",
    "pump_in_lockstep",
    "run_error_code",
    "without_answered_by",
]
