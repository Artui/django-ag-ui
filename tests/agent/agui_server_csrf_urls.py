from __future__ import annotations

from typing import Any

from django.urls import path
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import AGUIServer


class _Threads:
    """A conversation store reached only by ``DELETE`` in these tests."""

    async def delete(self, thread_id: str, *, request: Any) -> None:
        return None


class _Attachments:
    """An attachment store reached only by ``DELETE`` in these tests."""

    async def delete(self, attachment_id: str, *, request: Any) -> None:
        return None


class _Transcription:
    """A backend the 400-on-empty-body path answers before ever calling."""


_server = AGUIServer(
    ToolRegistry(),
    model=TestModel(),
    csrf_exempt=True,
    require_authenticated=False,
    conversation_store=_Threads(),
    attachment_store=_Attachments(),
    transcription_backend=_Transcription(),
)

urlpatterns = [path("assistant/", _server.urls)]
