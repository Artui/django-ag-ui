from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import ModelRequestContext


@dataclass
class ServingModelId(AbstractCapability[Any]):
    """The id of the model serving the latest attempt, which a streamed text end needs before
    the response recording it exists. ``ModelFallback`` moves by a new attempt, so it shows."""

    model_id: str | None = field(default=None, init=False)

    async def prepare_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        self.model_id = request_context.model_id or request_context.model.model_id
        return request_context


__all__ = ["ServingModelId"]
