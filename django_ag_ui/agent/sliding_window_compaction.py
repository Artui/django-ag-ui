"""``SlidingWindowCompaction`` — bound the message history sent to the model."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestContext


class SlidingWindowCompaction(AbstractCapability[Any]):
    """Caps the history at the newest ``max_messages`` before each model request.

    For long, tool-heavy runs (a drf-mcp data exploration, a durable thread
    replayed in full) the accumulated history dominates the token bill; this
    capability keeps only a window of the newest messages. The first message —
    the request that opened the conversation — is always kept, so the model
    retains the original ask.

    The cut never lands mid tool-exchange: it advances until the window starts
    at a request that carries no tool-return / retry parts, because a provider
    rejects a tool result whose originating call was trimmed away. Compose it
    through ``AgentConfig.capabilities`` / ``DJANGO_AG_UI["CAPABILITIES"]``::

        # settings-side: a module-level instance behind a dotted path
        COMPACTION = SlidingWindowCompaction(max_messages=40)
    """

    def __init__(self, max_messages: int) -> None:
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2 (the opener + a window)")
        self._max_messages = max_messages

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        request_context.messages = _window(request_context.messages, self._max_messages)
        return request_context


def _window(messages: list[ModelMessage], max_messages: int) -> list[ModelMessage]:
    if len(messages) <= max_messages:
        return messages
    # Reserve one slot for the pinned opener, then find a provider-valid start
    # for the tail window.
    cut = len(messages) - (max_messages - 1)
    while cut < len(messages) and not _valid_window_start(messages[cut]):
        cut += 1
    return [messages[0], *messages[cut:]]


def _valid_window_start(message: ModelMessage) -> bool:
    """Whether a window may start at ``message`` without orphaning a tool call.

    A response is always safe — its tool calls' returns *follow* it, so they
    stay inside the window. A request is safe unless it carries tool-return /
    retry parts, whose originating call sits before the cut.
    """
    if isinstance(message, ModelRequest):
        return not any(isinstance(part, ToolReturnPart | RetryPromptPart) for part in message.parts)
    return True


__all__ = ["SlidingWindowCompaction"]
