from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import RequestFactory
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.test import TestModel
from rest_framework_mcp import JsonRpcError, JsonRpcErrorCode

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
    # Advertised as an in-process function (not a deferred `external` call), so
    # Pydantic-AI's run loop actually invokes our `call_tool`.
    assert tool_def.kind == "function"


@pytest.mark.django_db
async def test_agent_run_executes_drf_tool_in_process() -> None:
    # The real regression: drive a full agent run. With `kind="external"` the
    # tool was deferred to the client and never executed (the run stalled); as a
    # `function` tool Pydantic-AI runs it in-process and returns its result.
    toolset = DrfMcpToolset(server, _request())
    agent = Agent(TestModel(call_tools=["add"]), toolsets=[toolset])
    result = await agent.run("add two numbers")
    returns = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "add"
    ]
    assert returns, "drf-mcp 'add' tool was deferred, not executed in-process"
    assert "result" in returns[0].content


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
    calls: list[str | None] = []

    def fake_list(cursor: str | None = None, **_kwargs: object) -> dict[str, object]:
        calls.append(cursor)
        return pages[len(calls) - 1]

    monkeypatch.setattr(server, "list_tools", fake_list)
    toolset = DrfMcpToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert {"p1", "p2"} <= set(tools)
    assert calls == [None, "c2", "c3"]

    # A second call is memoised — no further tools/list round-trips.
    await toolset.get_tools(None)  # type: ignore[arg-type]
    assert calls == [None, "c2", "c3"]


async def test_tools_list_error_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    error = JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "bad request")
    monkeypatch.setattr(server, "list_tools", lambda *_a, **_k: error)
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(RuntimeError, match="drf-mcp tools/list failed"):
        await toolset.get_tools(None)  # type: ignore[arg-type]


@pytest.mark.django_db
async def test_toolset_invokes_drf_tool_as_acting_user() -> None:
    toolset = DrfMcpToolset(server, _request())
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    result = await toolset.call_tool("add", {"a": 5, "b": 3}, None, tools["add"])
    assert result == {"result": 8}


async def test_call_tool_raises_on_a_protocol_fault() -> None:
    # Unknown tool is a genuine protocol fault — unrecoverable mid-run.
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(RuntimeError, match="drf-mcp tool 'nope'"):
        await toolset.call_tool("nope", {}, None, None)


async def test_malformed_arguments_raise_model_retry_with_detail() -> None:
    # JSON-RPC -32602 (the serializer rejecting the arguments *shape*) becomes
    # ``ModelRetry`` carrying the field errors, so the model self-corrects
    # instead of the run dying with RUN_ERROR.
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(ModelRetry, match="Invalid arguments") as excinfo:
        await toolset.call_tool("add", {"a": "not_a_number", "b": 1}, None, None)
    # The per-field DRF detail rides in the retry text for the model.
    assert "valid integer" in str(excinfo.value)


async def test_invalid_params_error_raises_model_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Twin of the test above driven at the payload level: on Python 3.11 the C
    # tracer drops the bridge frame across drf-mcp's real ``acall_tool`` executor
    # hop, leaving the ``INVALID_PARAMS`` → ``ModelRetry`` branch "uncovered" there
    # even though it runs — this monkeypatched twin records it reliably.
    async def fake_call(name: str, arguments: object = None, **_kwargs: object) -> JsonRpcError:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "Invalid arguments")

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(ModelRetry, match="Invalid arguments"):
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)


async def test_service_validation_error_result_raises_model_retry() -> None:
    # drf-mcp 0.7+ returns service-raised validation as an ``isError`` tool
    # result; the bridge still maps it to a retry.
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(ModelRetry, match="must be even"):
        await toolset.call_tool("invalid", {"a": 1, "b": 2}, None, None)


async def test_service_error_result_returns_model_readable_content() -> None:
    # A business-rule denial is content the model can read and act on — not
    # an exception that kills the chat.
    toolset = DrfMcpToolset(server, _request())
    result = await toolset.call_tool("denied", {"a": 1, "b": 2}, None, None)
    assert result == {"error": {"type": "service_error", "message": "denied by policy"}}


async def test_validation_error_payload_raises_model_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same branch as the integration test above, driven at the payload level:
    # Python 3.11's C tracer intermittently drops the bridge frames when the
    # call rides drf-mcp's real executor hop, leaving the branch "uncovered"
    # there even though it runs — this monkeypatched twin records reliably.
    import json as json_module

    async def fake_call(
        name: str, arguments: object = None, **_kwargs: object
    ) -> dict[str, object]:
        payload = {
            "error": {"type": "validation_error", "message": "bad", "detail": {"a": ["nope"]}}
        }
        return {"isError": True, "content": [{"type": "text", "text": json_module.dumps(payload)}]}

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DrfMcpToolset(server, _request())
    with pytest.raises(ModelRetry, match="bad.*nope"):
        await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)


async def test_unparseable_error_content_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(
        name: str, arguments: object = None, **_kwargs: object
    ) -> dict[str, object]:
        return {"isError": True, "content": [{"type": "text", "text": "not json"}]}

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DrfMcpToolset(server, _request())
    result = await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)
    assert result == {"error": {"type": "unknown", "message": "not json"}}


async def test_non_dict_error_payload_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(
        name: str, arguments: object = None, **_kwargs: object
    ) -> dict[str, object]:
        return {"isError": True, "content": [{"type": "text", "text": '{"error": "boom"}'}]}

    monkeypatch.setattr(server, "acall_tool", fake_call)
    toolset = DrfMcpToolset(server, _request())
    result = await toolset.call_tool("add", {"a": 1, "b": 2}, None, None)
    assert result == {"error": {"type": "unknown", "message": "boom"}}


async def test_excluded_names_are_skipped_registry_wins() -> None:
    # DUP-1: a name collision with the @tool registry must not reach the
    # agent — pydantic-ai raises UserError for duplicate tool names.
    toolset = DrfMcpToolset(server, _request(), exclude_names=frozenset({"add"}))
    tools = await toolset.get_tools(None)  # type: ignore[arg-type]
    assert "add" not in tools
    assert "denied" in tools


def test_retry_message_without_detail_is_the_bare_message() -> None:
    from django_ag_ui.integrations.drf_mcp import _retry_message

    assert _retry_message("nope", None) == "nope"
