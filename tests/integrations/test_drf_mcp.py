from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import RequestFactory
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError

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
    schema = tool_def.parameters_json_schema
    assert schema["type"] == "object"
    # Sourced from drf-mcp's own tools/list, so the merged inputSchema carries
    # the `additionalProperties` policy too (the old serializer-only path never
    # stamped it). `add` defaults to UnknownArguments.REJECT → a closed schema.
    assert schema["additionalProperties"] is False


async def test_loads_all_pages_from_tools_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drive the cursor loop: drf-mcp paginates tools/list, so the bridge must
    # follow `nextCursor` until it's exhausted.
    pages = [
        {"tools": [{"name": "p1", "inputSchema": {"type": "object"}}], "nextCursor": "c2"},
        {
            "tools": [{"name": "p2", "inputSchema": {"type": "object"}, "description": "two"}],
            "nextCursor": "c3",
        },
        {"tools": []},  # a trailing empty page exercises the zero-tools branch
    ]
    calls: list[dict[str, str] | None] = []

    def fake_list(params: dict[str, str] | None, _context: object) -> dict[str, object]:
        calls.append(params)
        return pages[len(calls) - 1]

    monkeypatch.setattr("django_ag_ui.integrations.drf_mcp.handle_tools_list", fake_list)
    toolset = DrfMcpToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert {"p1", "p2"} <= set(tools)
    assert calls == [None, {"cursor": "c2"}, {"cursor": "c3"}]

    # A second call is memoised — no further tools/list round-trips.
    await toolset.get_tools(None)  # type: ignore[arg-type]
    assert calls == [None, {"cursor": "c2"}, {"cursor": "c3"}]


async def test_tools_list_error_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    error = JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "bad request")
    monkeypatch.setattr(
        "django_ag_ui.integrations.drf_mcp.handle_tools_list",
        lambda _params, _context: error,
    )
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(RuntimeError, match="drf-mcp tools/list failed"):
        await toolset.get_tools(None)  # type: ignore[arg-type]


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
