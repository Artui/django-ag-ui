from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from ag_ui.core import BaseEvent, Interrupt, RunFinishedEvent, ToolCallStartEvent
from django_spec_agents import X_CONFIRM_KEY


async def stamp_approval_prompts(
    stream: AsyncIterator[BaseEvent],
    *,
    prompts: Mapping[str, str],
) -> AsyncIterator[BaseEvent]:
    """Forward ``stream``, adding a human-readable prompt to each approval interrupt."""
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
