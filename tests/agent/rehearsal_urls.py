from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.urls import path
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry

from django_ag_ui.agent.agui_server import AGUIServer
from tests.authed_request_factory import authenticated_user

_registry = ToolRegistry()


@tool(_registry)
def double(n: int) -> int:
    """Double a number."""
    return n * 2


def _get_user(_request: HttpRequest) -> Any:
    """Stands in for the token lookup the quickstart documents."""
    return authenticated_user()


_server = AGUIServer(_registry, get_user=_get_user, csrf_exempt=True)

urlpatterns = [path("agent/", _server.urls)]
