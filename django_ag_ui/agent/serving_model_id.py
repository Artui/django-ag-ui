from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext

from django_ag_ui.agent.message_model_id import MODEL_FALLBACK_FIELD


@dataclass
class ServingModelId(AbstractCapability[Any]):
    """The model serving the latest attempt, and whether a step's first model gave way to it.
    A streamed text end needs both before the response that records the id exists."""

    model_id: str | None = field(default=None, init=False)
    fallback: bool = field(default=False, init=False)
    _first: str | None = field(default=None, init=False)

    async def prepare_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        self.model_id = request_context.model_id or request_context.model.model_id
        if request_context.attempt == 1:
            self._first = self.model_id
        self.fallback = self.model_id != self._first
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        if not self.fallback:
            return response
        return replace(response, metadata={**(response.metadata or {}), MODEL_FALLBACK_FIELD: True})


__all__ = ["ServingModelId"]
