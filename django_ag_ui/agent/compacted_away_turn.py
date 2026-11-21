from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ag_ui.core import Message, UserMessage

from django_ag_ui.agent.continued_history import continued_history
from django_ag_ui.agent.outcome_agui_adapter import OutcomeAGUIAdapter


def compacted_away_turn(
    message_history: list[Any] | None,
    source_messages: Sequence[Message] | None,
    run_messages: list[Message],
) -> str | None:
    """Why a new turn is refused: it edits a prompt the row holds and the snapshot lost."""
    if message_history is None or not source_messages:
        return None
    held = {m.id for m in source_messages if isinstance(m, UserMessage)}
    seen = {message.id for message in OutcomeAGUIAdapter.dump_messages(message_history)}
    _, new_turn = continued_history(message_history, run_messages)
    for message in new_turn:
        if message.id in held and message.id not in seen:
            return (
                f"Message {message.id} was compacted out of the history this run resumes,"
                " so it can no longer be edited or regenerated."
            )
    return None


__all__ = ["compacted_away_turn"]
