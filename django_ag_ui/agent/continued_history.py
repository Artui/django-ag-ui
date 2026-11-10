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
    """The part of ``history`` a server-held run keeps, and the client's new turn as posted."""
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
    """The id the adapter gives the request the new turn loads into: its last contributor's."""
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
    """Whether ``message`` is a stored user turn the client resubmitted under ``turn_id``."""
    if not isinstance(message, ModelRequest):
        return False
    if not any(isinstance(part, UserPromptPart) for part in message.parts):
        return False
    return AGUIAdapter.dump_messages([message])[-1].id == turn_id


def _is_tool_result(part: object) -> TypeGuard[ToolReturnPart | RetryPromptPart]:  # ty: ignore[deprecated]
    """Whether ``part`` answers a tool call, as the adapter's cut counts one."""
    if isinstance(part, ToolReturnPart):
        return True
    return isinstance(part, RetryPromptPart) and part.tool_name is not None  # ty: ignore[deprecated]


__all__ = ["continued_history"]
