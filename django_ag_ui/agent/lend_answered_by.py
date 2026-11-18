from __future__ import annotations

from collections.abc import Sequence

from ag_ui.core import Message, ToolMessage

from django_ag_ui.agent.stamp_answered_by import ANSWERED_BY_FIELD, stamp_answered_by


def lend_answered_by(messages: Sequence[Message], stored: Sequence[Message]) -> list[Message]:
    """``messages``, each tool result marked as ``stored`` marks the same call, where it does."""
    marks = {
        message.tool_call_id: mark
        for message in stored
        if isinstance(message, ToolMessage)
        and isinstance(mark := (message.metadata or {}).get(ANSWERED_BY_FIELD), str)
    }
    return [
        stamp_answered_by(message, marks[message.tool_call_id])
        if isinstance(message, ToolMessage) and message.tool_call_id in marks
        else message
        for message in messages
    ]


__all__ = ["lend_answered_by"]
