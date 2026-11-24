from __future__ import annotations

from typing import Any


def without_subagent_messages(run_input: Any) -> Any:
    """``run_input`` without the messages a sub-agent's tagged events built: not history."""
    kept = [message for message in run_input.messages if message.subagent_run_id is None]
    if len(kept) == len(run_input.messages):
        return run_input
    return run_input.model_copy(update={"messages": kept})


__all__ = ["without_subagent_messages"]
