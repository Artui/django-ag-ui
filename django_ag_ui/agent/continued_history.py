"""``continued_history`` — what a server-held run continues from, and the turn it adds."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeGuard

from ag_ui.core import AssistantMessage, Message, ToolMessage
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,  # ty: ignore[deprecated]
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui._adapter import pending_tool_calls
from pydantic_ai.ui.ag_ui import AGUIAdapter


def continued_history(
    history: Sequence[ModelMessage], posted: Sequence[Message]
) -> tuple[list[ModelMessage], list[Message]]:
    """The part of ``history`` a server-held run keeps, and the client's new turn as posted.

    A run seeded from a snapshot sets the adapter's
    ``manage_message_history="server"``, under which the adapter runs on the
    stored history plus only the client's new turn, and cuts the history where
    the client resubmitted a stored user message. It decides both in private
    helpers and reports neither, so the stored thread cannot be built from what
    the run used; this mirrors the rule on the posted messages instead, and
    ``test_the_stored_row_reads_the_way_the_run_did`` holds the two together.

    - **The new turn** is every posted message after the last one that loads
      into a model response: an assistant or reasoning message, or an activity
      carrying a provider's part. Asked of pydantic-ai one message at a time
      rather than read off the role, so an activity counts on whichever side
      the installed adapter puts it.
    - **The cut.** The new turn loads into one request, which carries the id
      of the *last* message folded into it -- not the first, so a tool result
      posted ahead of an edited prompt still lets the prompt name its turn.
      When a stored request holding a user prompt carries that id, the client
      resubmitted it (an edit, a regenerate, a retry): the history ends just
      before that request, keeping the tool results it carried, which the run
      merged in from the turn before.
    - **What is stored of the new turn** is what the client posted, verbatim,
      so the attachment refs riding a user message survive. The one exception
      is a tool result the kept history already answers -- the one the cut
      request carried, or one the stored thread holds from an earlier turn: the
      adapter drops the client's copy of it, and storing it would record one
      call answered twice, under two ids. That filter is one arc over two
      conditions: without the tool-message check
      ``test_an_edited_message_cuts_the_history_before_its_turn`` fails, and
      without the call check
      ``test_only_the_client_copy_of_a_kept_result_is_left_out`` fails.
      Without the answers read from the whole kept history rather than the cut
      request alone,
      ``test_an_answered_result_resent_without_a_cut_is_left_out`` fails.

    The adapter also drops tool results for calls nothing is waiting on, and
    a client's compaction parts; those are stored as posted, because the
    server never persists less than the client sent.
    """
    new_turn = list(posted[_after_last_response(posted) :])
    turn_id = _turn_id(new_turn, posted, history)
    cut = None
    if turn_id is not None:
        cut = next(
            (index for index, message in enumerate(history) if _names(message, turn_id)), None
        )
    kept = list(history)
    if cut is not None:
        results: list[ModelRequestPart] = [
            part for part in history[cut].parts if _is_tool_result(part)
        ]
        kept = [*history[:cut], *([ModelRequest(parts=results)] if results else [])]
    answered = {
        part.tool_call_id
        for message in kept
        if isinstance(message, ModelRequest)
        for part in message.parts
        if _is_tool_result(part)
    }
    resubmitted = [
        message
        for message in new_turn
        if not (isinstance(message, ToolMessage) and message.tool_call_id in answered)
    ]
    return kept, resubmitted


def _after_last_response(posted: Sequence[Message]) -> int:
    """The index just past the last posted message that loads into a model response."""
    for index in range(len(posted) - 1, -1, -1):
        message = posted[index]
        # A tool result is always a request part, and on its own it cannot load
        # at all: it needs the call it answers.
        if isinstance(message, ToolMessage):
            continue
        if any(
            isinstance(loaded, ModelResponse) for loaded in AGUIAdapter.load_messages([message])
        ):
            return index + 1
    return 0


def _turn_id(
    new_turn: Sequence[Message], posted: Sequence[Message], history: Sequence[ModelMessage]
) -> str | None:
    """The id the adapter gives the request the new turn loads into: its last contributor's.

    A message contributes when it loads into a part. A tool result does in the
    run, though not on its own, since it needs its call -- unless the adapter
    drops it first: a result for a call that is neither among the posted
    assistant messages' calls nor pending in ``history`` is dropped before the
    turn loads, and a dropped result names nothing. The guard is one arc over
    three conditions: without the tool-message check
    ``test_a_tool_result_after_the_last_response_is_part_of_the_new_turn``
    fails, without ``in called``
    ``test_a_result_for_a_posted_call_names_the_turn_though_its_call_is_answered``,
    without ``in pending`` ``test_a_result_for_a_pending_call_still_names_the_turn``,
    and without either ``test_a_stale_result_does_not_name_the_turn``; without
    the load check ``test_a_message_that_loads_nothing_does_not_name_the_turn``.
    """
    called = {
        call.id
        for message in posted
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls or ()
    }
    pending = {call.tool_call_id for call in pending_tool_calls(history)}
    for message in reversed(new_turn):
        if isinstance(message, ToolMessage):
            if message.tool_call_id in called or message.tool_call_id in pending:
                return message.id
        elif AGUIAdapter.load_messages([message]):
            return message.id
    return None


def _names(message: ModelMessage, turn_id: str) -> bool:
    """Whether ``message`` is a stored user turn the client resubmitted under ``turn_id``.

    The id is read back through the public dump, which restores a loaded
    message's id on the last message it dumps, rather than out of the reserved
    metadata namespace pydantic-ai keeps it in. A message the adapter never
    loaded dumps under a fresh id, which names nothing the client holds.
    """
    if not isinstance(message, ModelRequest):
        return False
    if not any(isinstance(part, UserPromptPart) for part in message.parts):
        return False
    return AGUIAdapter.dump_messages([message])[-1].id == turn_id


def _is_tool_result(part: object) -> TypeGuard[ToolReturnPart | RetryPromptPart]:  # ty: ignore[deprecated]
    """Whether ``part`` answers a tool call, as the adapter's cut counts one.

    A legacy retry prompt bound to a tool answers its call the way a return
    does; one bound to no tool is feedback on a whole response, and keeping it
    fails ``test_a_legacy_retry_bound_to_a_tool_is_kept_as_a_result``.
    """
    if isinstance(part, ToolReturnPart):
        return True
    return isinstance(part, RetryPromptPart) and part.tool_name is not None  # ty: ignore[deprecated]


__all__ = ["continued_history"]
