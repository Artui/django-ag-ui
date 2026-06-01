from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import RequestFactory

from django_ag_ui.integrations.drf_mcp import DrfMcpToolset
from tests.integrations.drf_server import server


def _request() -> HttpRequest:
    request = RequestFactory().post("/agent/")
    request.user = AnonymousUser()  # type: ignore[attr-defined]
    return request


async def test_toolset_exposes_drf_tools_with_schemas() -> None:
    toolset = DrfMcpToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert "add" in tools
    tool_def = tools["add"].tool_def
    assert tool_def.name == "add"
    assert tool_def.parameters_json_schema["type"] == "object"


@pytest.mark.django_db
async def test_toolset_invokes_drf_tool_as_acting_user() -> None:
    toolset = DrfMcpToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    result = await toolset.call_tool("add", {"a": 5, "b": 3}, None, tools["add"])
    assert result == {"result": 8}


async def test_call_tool_raises_on_a_drf_error() -> None:
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(RuntimeError, match="drf-mcp tool 'nope'"):
        await toolset.call_tool("nope", {}, None, None)
