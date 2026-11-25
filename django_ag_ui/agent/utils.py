from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Collection
from datetime import timedelta
from typing import Any

from ag_ui.core import Message, ToolMessage
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django_spec_agents import IdentifierTooLongError, ResumableRunStore, RunNotParkedError
from django_spec_agents.persistence.types.run_claim import RunClaim
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_store import RunStore
from django_spec_agents.persistence.types.thread_run_state import ThreadRunState
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus

from django_ag_ui.agent.partial_answer_error import PartialAnswerError
from django_ag_ui.agent.stamp_answered_by import ANSWERED_BY_FIELD
from django_ag_ui.agent.thread_busy_error import ThreadBusyError
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


def run_error_message(error: Exception, message: str) -> str:
    """``message``, or the loop a ``DetectLoops`` stop names when ``error`` is or groups one."""
    try:
        from pydantic_ai_harness.detect_loops import LoopDetectedError
    except ImportError:
        return message
    found = error.subgroup(LoopDetectedError) if isinstance(error, BaseExceptionGroup) else error
    while isinstance(found, BaseExceptionGroup):
        found = found.exceptions[0]
    if not isinstance(found, LoopDetectedError):
        return message
    return f"The run was stopped in a loop ({found.loop.pattern}): {found}"


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
    """Whether a run log event is what a queued start kept for its worker, never sent out."""
    return (
        isinstance(event, dict)
        and event.get("type") == "CUSTOM"
        and event.get("name") == QUEUED_INPUT_EVENT
    )


def is_permission_refusal(error: BaseException) -> bool:
    """Whether ``error`` refuses the principal: ``PermissionDenied`` or ``NotPermitted``."""
    if isinstance(error, PermissionDenied):
        return True
    try:
        from django_service_specs.authorization.not_permitted import NotPermitted
    except ImportError:
        return False
    return isinstance(error, NotPermitted)


def queued_record(value: dict[str, Any]) -> dict[str, Any]:
    """The run log entry a queued start keeps for its worker; no client is sent it."""
    return {"type": "CUSTOM", "name": QUEUED_INPUT_EVENT, "value": value}


async def kept_for_worker(
    runs: RunStore, run_id: str, request: HttpRequest
) -> dict[str, Any] | None:
    """What a queued start kept as ``run_id``'s first log entry; ``None`` if it kept nothing."""
    entries = await runs.read(run_id, request=request, limit=1)
    if not entries or not is_queued_input(entries[0].event):
        return None
    return entries[0].event["value"]


async def claim_thread(
    store: RunStore,
    thread_id: str,
    run_id: str,
    *,
    request: HttpRequest,
    lease: timedelta,
    resumes: str | None = None,
) -> RunClaim:
    """Claim the thread; ``resumes`` a parked run only once, where the store can say so."""
    if isinstance(store, ResumableRunStore):
        return await store.claim(thread_id, run_id, request=request, lease=lease, resumes=resumes)
    return await store.claim(thread_id, run_id, request=request, lease=lease)


def parked_run(state: ThreadRunState | None) -> str | None:
    """The run ``state`` is parked on, read before a claim makes the thread running."""
    if state is None or state.status is not ThreadRunStatus.WAITING:
        return None
    return state.last_run_id


def left_open(
    state: ThreadRunState | None, answered: Collection[str], *, answering: bool
) -> tuple[str, ...]:
    """The parked calls an answer leaves open; none unless it answers, or names, one of them."""
    waiting = [] if state is None else [call.tool_call_id for call in state.waiting]
    if not answering and not set(answered) & set(waiting):
        return ()
    return tuple(call for call in waiting if call not in answered)


def answered_calls(run_input: Any) -> set[str]:
    """The calls a browser input answers: its ``resume`` entries and its tool messages."""
    resumed = {entry.interrupt_id.removeprefix("int-") for entry in run_input.resume or ()}
    told = {m.tool_call_id for m in run_input.messages if isinstance(m, ToolMessage)}
    return resumed | told


async def claim_headless(
    store: RunStore,
    thread_id: str,
    run_id: str,
    *,
    request: HttpRequest,
    lease: timedelta,
    answers: Collection[str] | None,
) -> bool:
    """Claim for a headless start, ``True`` if it resumed a park, which it does only once."""
    state = None if answers is None else await store.get(thread_id, request=request)
    unanswered = left_open(state, answers or (), answering=True)
    if unanswered:
        raise PartialAnswerError(thread_id, unanswered)
    resumes = parked_run(state)
    try:
        claim = await claim_thread(
            store, thread_id, run_id, request=request, lease=lease, resumes=resumes
        )
    except RunNotParkedError as error:
        if error.active_run_id is None:
            raise
        raise ThreadBusyError(thread_id, error.active_run_id) from error
    if not claim.claimed:
        raise ThreadBusyError(thread_id, claim.active_run_id)
    return resumes is not None


async def give_back(store: RunStore, thread_id: str, run_id: str, *, request: HttpRequest) -> None:
    """Release a claim the store can, so the thread keeps its state, a park too; else fail it."""
    if isinstance(store, ResumableRunStore) and await store.release(
        thread_id, run_id, request=request
    ):
        return
    await store.finish(thread_id, run_id, RunOutcome.ERROR, request=request)


TASK_TEXT_LIMIT = 8000


def task_text(key: str, text: str) -> dict[str, Any]:
    """``{key: text}`` for a task step, cut to ``TASK_TEXT_LIMIT`` with ``truncated`` if it was."""
    if len(text) <= TASK_TEXT_LIMIT:
        return {key: text}
    return {key: text[:TASK_TEXT_LIMIT], "truncated": True}


__all__ = [
    "answered_calls",
    "claim_headless",
    "left_open",
    "claim_thread",
    "give_back",
    "parked_run",
    "is_permission_refusal",
    "is_queued_input",
    "kept_for_worker",
    "queued_record",
    "ERROR_CODE",
    "IDENTIFIER_TOO_LONG_CODE",
    "INTERRUPTED_CODE",
    "RUN_ERROR_CODES",
    "run_error_message",
    "STREAM_END",
    "TASK_TEXT_LIMIT",
    "TIMED_OUT_CODE",
    "a2ui_validator",
    "discard_tasks",
    "pump_in_lockstep",
    "run_error_code",
    "task_text",
    "without_answered_by",
]
