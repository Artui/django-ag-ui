from __future__ import annotations

import json
from typing import Any

from django.test import RequestFactory, override_settings

from django_ag_ui import ToolRegistry, tool
from django_ag_ui.agent.tools_view import ToolsView


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg, summary="Look up a user")
    def find_user(email: str) -> dict[str, Any]:
        """Find a user by email."""
        return {}

    return reg


@override_settings(DJANGO_AG_UI={})
def test_get_returns_the_tool_catalog() -> None:
    view = ToolsView(_registry())
    response = view(RequestFactory().get("/agent/tools/"))
    assert response.status_code == 200
    assert json.loads(response.content) == [
        {"name": "find_user", "summary": "Look up a user", "description": "Find a user by email."},
    ]


def test_non_get_is_rejected() -> None:
    response = ToolsView(ToolRegistry())(RequestFactory().post("/agent/tools/"))
    assert response.status_code == 405


def test_anonymous_is_rejected_when_require_authenticated() -> None:
    view = ToolsView(_registry(), require_authenticated=True)
    response = view(RequestFactory().get("/agent/tools/"))
    assert response.status_code == 401


@override_settings(DJANGO_AG_UI={})
def test_get_user_hook_opens_the_catalog() -> None:
    from types import SimpleNamespace

    view = ToolsView(
        _registry(),
        require_authenticated=True,
        get_user=lambda _request: SimpleNamespace(is_authenticated=True),
    )
    response = view(RequestFactory().get("/agent/tools/"))
    assert response.status_code == 200
