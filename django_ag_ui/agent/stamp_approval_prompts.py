"""``stamp_approval_prompts`` — give a gated call a question a person can read."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from ag_ui.core import BaseEvent, Interrupt, RunFinishedEvent, ToolCallStartEvent
from django_pydantic_agent.constants import X_CONFIRM_KEY


async def stamp_approval_prompts(
    stream: AsyncIterator[BaseEvent],
    *,
    prompts: Mapping[str, str],
) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, adding a human-readable prompt to each approval interrupt.

    A gated tool defers instead of running, and the interrupt announcing it
    carries the question the client will ask. Pydantic-AI generates that question
    from the call itself — ``Approve create_event({"title": "Design sync"})?`` —
    which is accurate and not something to put in front of a person. The client
    already reads ``x-confirm`` off a tool's schema for the confirmation it gates
    in the browser, so the same key is stamped here, on the interrupt's
    ``metadata``: one concept for both gates, whichever end does the gating.

    Where the phrase comes from, in order: an explicit ``APPROVAL_PROMPTS`` entry,
    then the tool's own ``confirm=`` (already on its schema as ``x-confirm``, and
    resolved into ``prompts`` by :class:`AGUIServer`). A tool with neither keeps
    the generated question, and an interrupt that already carries an ``x-confirm``
    of its own is left alone — a tool that raised
    :class:`~pydantic_ai.exceptions.ApprovalRequired` with its own metadata has
    said something more specific than any static mapping can.

    Interrupts name a ``tool_call_id`` rather than a tool, so the names are
    learned from the ``TOOL_CALL_START`` events that precede them in the same
    stream. That is why this wraps the stream rather than post-processing the
    terminal event alone.
    """
    names: dict[str, str] = {}
    async for event in stream:
        if isinstance(event, ToolCallStartEvent):
            names[event.tool_call_id] = event.tool_call_name
        elif isinstance(event, RunFinishedEvent):
            event = _stamped(event, names=names, prompts=prompts)
        yield event


def _stamped(
    event: RunFinishedEvent,
    *,
    names: Mapping[str, str],
    prompts: Mapping[str, str],
) -> RunFinishedEvent:
    """A copy of ``event`` whose interrupts carry their prompts, or ``event`` itself."""
    outcome = event.outcome
    interrupts = getattr(outcome, "interrupts", None)
    if outcome is None or interrupts is None:
        return event
    stamped = [_with_prompt(interrupt, names=names, prompts=prompts) for interrupt in interrupts]
    if stamped == list(interrupts):
        return event
    return event.model_copy(update={"outcome": outcome.model_copy(update={"interrupts": stamped})})


def _with_prompt(
    interrupt: Interrupt,
    *,
    names: Mapping[str, str],
    prompts: Mapping[str, str],
) -> Interrupt:
    metadata = interrupt.metadata or {}
    if metadata.get(X_CONFIRM_KEY):
        return interrupt
    tool_call_id = interrupt.tool_call_id
    if tool_call_id is None:
        return interrupt
    prompt = prompts.get(names.get(tool_call_id, ""))
    if not prompt:
        return interrupt
    return interrupt.model_copy(update={"metadata": {**metadata, X_CONFIRM_KEY: prompt}})


__all__ = ["stamp_approval_prompts"]
