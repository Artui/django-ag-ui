from __future__ import annotations

from typing import Any

from ag_ui.core import AssistantMessage, Message

MODEL_ID_FIELD = "model_id"
"""The ``metadata`` key naming the model that gave an assistant message, streamed and stored.

The value is pydantic-ai's ``ModelResponse.model_id``: the id the model was configured
by (``"openai:gpt-5"``), so a fallback answer names the fallback model. The key is left
out when pydantic-ai reports none, and on messages that carry no text.
"""


def stamp_model_id(message: Any, model_id: str | None) -> Any:
    """A copy of ``message`` carrying ``model_id`` in its ``metadata``, or itself when none."""
    if model_id is None:
        return message
    metadata = {**(message.metadata or {}), MODEL_ID_FIELD: model_id}
    return message.model_copy(update={"metadata": metadata})


def with_dumped_model_id(model_id: str | None, dumped: list[Message]) -> list[Message]:
    """``dumped``, each assistant message with text stamped with its response's ``model_id``."""
    return [
        stamp_model_id(message, model_id)
        if isinstance(message, AssistantMessage) and message.content is not None
        else message
        for message in dumped
    ]


__all__ = ["MODEL_ID_FIELD", "stamp_model_id", "with_dumped_model_id"]
