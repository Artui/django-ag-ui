"""A root URLconf mounting an ``AGUIServer(csrf_exempt=True)`` with every write route.

Used by ``test_agui_server`` to drive the mount through a *real*
``CsrfViewMiddleware``. The flag assertions elsewhere check what the views
declare; this checks what Django does with the declaration, which is the half
that was wrong — the run endpoint carried the flag and the five write routes
beside it did not, so each one 403'd before reaching its view.

The stores are minimal fakes rather than the contrib models: every route here is
driven with an empty body so it answers before it touches storage, except the two
``DELETE``s, which need only an awaitable ``delete``.
"""

from __future__ import annotations

from typing import Any

from django.urls import path
from django_pydantic_agent.registry.tool_registry import ToolRegistry
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
