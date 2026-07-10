"""``ClearToolResults`` — blank stale tool outputs instead of trimming history."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models import ModelRequestContext

_CLEARED = "(tool result cleared to save context)"


class ClearToolResults(AbstractCapability[Any]):
    """Replaces all but the newest ``keep_last`` tool results with a stub.

    The structure-preserving alternative to
    :class:`~django_ag_ui.agent.sliding_window_compaction.SlidingWindowCompaction`:
    every message and tool call stays in the history (so the model still sees
    *what it did*), but stale tool outputs — usually the bulk of a tool-heavy
    run's tokens — collapse to a one-line placeholder. Always provider-valid,
    since no message is removed.

    Compose it through ``AgentConfig.capabilities`` /
    ``DJANGO_AG_UI["CAPABILITIES"]`` like any capability.
    """

    def __init__(self, keep_last: int = 3) -> None:
        if keep_last < 0:
            raise ValueError("keep_last must be non-negative")
        self._keep_last = keep_last

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        request_context.messages = _clear_stale(request_context.messages, self._keep_last)
        return request_context


def _clear_stale(messages: list[ModelMessage], keep_last: int) -> list[ModelMessage]:
    total = sum(
        1
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    to_clear = total - keep_last
    if to_clear <= 0:
        return messages
    cleared: list[ModelMessage] = []
    for message in messages:
        if to_clear > 0 and isinstance(message, ModelRequest):
            parts: list[Any] = []
            for part in message.parts:
                if to_clear > 0 and isinstance(part, ToolReturnPart):
                    parts.append(replace(part, content=_CLEARED))
                    to_clear -= 1
                else:
                    parts.append(part)
            cleared.append(replace(message, parts=parts))
        else:
            cleared.append(message)
    return cleared


__all__ = ["ClearToolResults"]
