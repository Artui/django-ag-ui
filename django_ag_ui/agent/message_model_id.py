from __future__ import annotations

from typing import Any

from ag_ui.core import AssistantMessage, Message
from pydantic_ai.messages import ModelResponse

MODEL_ID_FIELD = "model_id"
"""The ``metadata`` key naming the model that gave an assistant message, streamed and stored.

The value is pydantic-ai's ``ModelResponse.model_id``: the id the model was configured
by (``"openai:gpt-5"``), so a fallback answer names the fallback model. The key is left
out when pydantic-ai reports none, and on messages that carry no text.
"""

MODEL_FALLBACK_FIELD = "model_fallback"
"""The ``metadata`` key set to ``true`` when a model other than the request's first one answered.

Absent otherwise. It is also the key the response's own ``ModelResponse.metadata`` keeps
the mark under, which is how a stored copy and a replay know it.
"""


def stamp_model_id(message: Any, model_id: str | None, *, fallback: bool = False) -> Any:
    """A copy of ``message`` carrying ``model_id`` (and the fallback mark), or itself when none."""
    if model_id is None:
        return message
    metadata = {**(message.metadata or {}), MODEL_ID_FIELD: model_id}
    if fallback:
        metadata[MODEL_FALLBACK_FIELD] = True
    return message.model_copy(update={"metadata": metadata})


def with_dumped_model_id(response: ModelResponse, dumped: list[Message]) -> list[Message]:
    """``dumped``, each assistant message with text stamped with ``response``'s model."""
    fallback = (response.metadata or {}).get(MODEL_FALLBACK_FIELD) is True
    return [
        stamp_model_id(message, response.model_id, fallback=fallback)
        if isinstance(message, AssistantMessage) and message.content is not None
        else message
        for message in dumped
    ]


__all__ = ["MODEL_FALLBACK_FIELD", "MODEL_ID_FIELD", "stamp_model_id", "with_dumped_model_id"]
