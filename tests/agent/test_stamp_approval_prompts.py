"""The question a gated call asks a person.

Pydantic-AI generates the interrupt's question from the call itself
(``Approve create_event({"title": "Design sync"})?``), which is accurate and not
something to show. The transformer under test replaces it with the same
``x-confirm`` the browser already reads for a confirmation it gates itself, so
one concept covers both gates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ag_ui.core import (
    BaseEvent,
    Interrupt,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    TextMessageContentEvent,
    ToolCallStartEvent,
)
from django_pydantic_agent.constants import X_CONFIRM_KEY

from django_ag_ui.agent.stamp_approval_prompts import stamp_approval_prompts

PROMPTS = {"create_event": "Book Design sync on Friday at 14:00?"}


def _start(tool_call_id: str, name: str) -> ToolCallStartEvent:
    return ToolCallStartEvent(tool_call_id=tool_call_id, tool_call_name=name)


def _interrupted(*interrupts: Interrupt) -> RunFinishedEvent:
    return RunFinishedEvent(
        thread_id="t1",
        run_id="r1",
        outcome=RunFinishedInterruptOutcome(interrupts=list(interrupts)),
    )


def _interrupt(tool_call_id: str | None = "call-1", **extra: object) -> Interrupt:
    return Interrupt(
        id=f"int-{tool_call_id}",
        reason="tool_call",
        tool_call_id=tool_call_id,
        message='Approve create_event({"title": "Design sync"})?',
        **extra,  # type: ignore[arg-type]
    )


async def _run(events: list[BaseEvent], prompts: dict[str, str] = PROMPTS) -> list[BaseEvent]:
    async def _stream() -> AsyncIterator[BaseEvent]:
        for event in events:
            yield event

    return [event async for event in stamp_approval_prompts(_stream(), prompts=prompts)]


def _interrupts(event: BaseEvent) -> list[Interrupt]:
    outcome = getattr(event, "outcome", None)
    return list(getattr(outcome, "interrupts", []) or [])


async def test_stamps_the_prompt_for_the_tool_the_interrupt_belongs_to() -> None:
    out = await _run([_start("call-1", "create_event"), _interrupted(_interrupt())])

    assert _interrupts(out[-1])[0].metadata == {X_CONFIRM_KEY: PROMPTS["create_event"]}


async def test_leaves_the_generated_question_in_place() -> None:
    """The stamp is additive: a client with no use for it still has ``message``."""
    out = await _run([_start("call-1", "create_event"), _interrupted(_interrupt())])

    assert _interrupts(out[-1])[0].message == 'Approve create_event({"title": "Design sync"})?'


async def test_a_tool_with_no_prompt_is_left_alone() -> None:
    out = await _run([_start("call-1", "delete_thing"), _interrupted(_interrupt())])

    assert _interrupts(out[-1])[0].metadata is None


async def test_does_not_overwrite_a_prompt_the_tool_supplied_itself() -> None:
    """A tool that raised ``ApprovalRequired`` with its own metadata knows more."""
    own = _interrupt(metadata={X_CONFIRM_KEY: "Delete the Basalt room for good?"})

    out = await _run([_start("call-1", "create_event"), _interrupted(own)])

    assert _interrupts(out[-1])[0].metadata == {X_CONFIRM_KEY: "Delete the Basalt room for good?"}


async def test_keeps_other_metadata_the_interrupt_carries() -> None:
    out = await _run(
        [_start("call-1", "create_event"), _interrupted(_interrupt(metadata={"a": 1}))]
    )

    assert _interrupts(out[-1])[0].metadata == {"a": 1, X_CONFIRM_KEY: PROMPTS["create_event"]}


async def test_stamps_each_interrupt_of_a_batch_from_its_own_tool() -> None:
    prompts = {"create_event": "Book it?", "move_event": "Move it?"}
    out = await _run(
        [
            _start("call-1", "create_event"),
            _start("call-2", "move_event"),
            _interrupted(_interrupt("call-1"), _interrupt("call-2")),
        ],
        prompts,
    )

    assert [interrupt.metadata for interrupt in _interrupts(out[-1])] == [
        {X_CONFIRM_KEY: "Book it?"},
        {X_CONFIRM_KEY: "Move it?"},
    ]


async def test_an_interrupt_with_no_tool_call_id_cannot_be_matched() -> None:
    out = await _run([_start("call-1", "create_event"), _interrupted(_interrupt(None))])

    assert _interrupts(out[-1])[0].metadata is None


async def test_leaves_a_successful_run_finished_untouched() -> None:
    finished = RunFinishedEvent(thread_id="t1", run_id="r1", outcome=RunFinishedSuccessOutcome())

    out = await _run([_start("call-1", "create_event"), finished])

    assert out[-1] is finished


async def test_leaves_a_run_finished_with_no_outcome_untouched() -> None:
    """A producer written before the interrupt-aware lifecycle omits it entirely."""
    finished = RunFinishedEvent(thread_id="t1", run_id="r1")

    out = await _run([finished])

    assert out[-1] is finished


async def test_forwards_every_event_in_order() -> None:
    # Something in the middle that is neither a tool-call start nor a terminal
    # event: the wrapper sits on the whole stream, so it must not touch the rest.
    events: list[BaseEvent] = [
        _start("call-1", "create_event"),
        TextMessageContentEvent(message_id="m1", delta="one moment"),
        _interrupted(_interrupt()),
    ]

    out = await _run(events)

    assert [event.type for event in out] == [event.type for event in events]
