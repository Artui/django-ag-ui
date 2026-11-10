from __future__ import annotations

import json
import warnings
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django_service_specs import Parameter, Parameters, ServiceSpec, Unrestricted
from django_spec_agents.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_mcp import MCPServer
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory
from tests.integrations.spec_mcp_server import server as spec_mcp_server


def _run_input(content: str) -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": content}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


def _post(content: str) -> Any:
    return AuthedRequestFactory().post(
        "/agent/", data=_run_input(content), content_type="application/json"
    )


async def _events(response: StreamingHttpResponse) -> list[dict[str, Any]]:
    chunks: list[str] = []
    async for chunk in response.streaming_content:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    lines = "".join(chunks).splitlines()
    return [json.loads(line[6:]) for line in lines if line.startswith("data: ")]


def _calling(name: str, args: dict[str, Any]) -> FunctionModel:
    """Calls ``name`` once, then answers once the call has a result."""

    async def stream_fn(messages, info):  # noqa: ANN001, ANN202
        if any(part.part_kind == "tool-return" for part in messages[-1].parts):
            yield "done"
            return
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id="c1")}

    return FunctionModel(stream_function=stream_fn)


def _views(server: AGUIServer) -> dict[str, Any]:
    patterns, _, _ = server.urls
    return {pattern.name: pattern.callback for pattern in patterns}


async def test_a_spec_mcp_servers_tool_runs_through_the_endpoint() -> None:
    server = AGUIServer(
        ToolRegistry(), model=_calling("add", {"a": 2, "b": 3}), mcp_server=spec_mcp_server
    )
    endpoint = _views(server)["endpoint"]

    events = await _events(
        await endpoint(
            AuthedRequestFactory().post(
                "/agent/", data=_run_input("add 2 and 3"), content_type="application/json"
            )
        )
    )

    assert "RUN_ERROR" not in [event["type"] for event in events]
    assert events[-1]["type"] == "RUN_FINISHED"
    (result,) = [event for event in events if event["type"] == "TOOL_CALL_RESULT"]
    # The service's own return, carried as django-spec-mcp's structured content.
    assert json.loads(result["content"]) == {"result": 5}
    assert result.get("outcome", "absent") == "absent"


async def test_a_spec_mcp_refusal_streams_as_a_failed_call() -> None:
    # MCPToolset, not a pass-through: a refusal is a failed call with the service's sentence.
    server = AGUIServer(
        ToolRegistry(), model=_calling("denied", {"a": 1, "b": 2}), mcp_server=spec_mcp_server
    )
    endpoint = _views(server)["endpoint"]

    events = await _events(
        await endpoint(
            AuthedRequestFactory().post(
                "/agent/", data=_run_input("try it"), content_type="application/json"
            )
        )
    )

    (result,) = [event for event in events if event["type"] == "TOOL_CALL_RESULT"]
    assert result.get("outcome") == "failed"
    assert "denied by policy" in result["content"]


async def test_a_value_the_spec_refuses_streams_as_a_retry() -> None:
    # A retry, not a failure: no outcome, and the kernel's sentence reaches the model to correct it.
    server = AGUIServer(
        ToolRegistry(), model=_calling("invalid", {"a": 1, "b": 2}), mcp_server=spec_mcp_server
    )

    events = await _events(await _views(server)["endpoint"](_post("try it")))

    assert events[-1]["type"] == "RUN_FINISHED"
    (result,) = [event for event in events if event["type"] == "TOOL_CALL_RESULT"]
    assert result.get("outcome", "absent") == "absent"
    assert "must be even" in result["content"]


async def test_the_tool_guard_gates_a_bridged_service() -> None:
    # A ServiceSpec mutates, so the guard defers the call to the client's approval.
    server = AGUIServer(
        ToolRegistry(),
        model=_calling("add", {"a": 2, "b": 3}),
        mcp_server=spec_mcp_server,
        config=build_ag_ui_config(tool_guard=ToolGuardConfig(enabled=True)),
    )

    events = await _events(await _views(server)["endpoint"](_post("add 2 and 3")))

    assert "TOOL_CALL_RESULT" not in [event["type"] for event in events]
    assert events[-1]["type"] == "RUN_FINISHED"
    assert events[-1]["outcome"]["type"] == "interrupt"


async def test_a_registry_tool_beats_a_bridged_tool_of_the_same_name() -> None:
    registry = ToolRegistry()

    @tool(registry)
    def add(a: int, b: int) -> int:
        """A registry tool shadowing the bridged one."""
        return a + b

    view = DjangoAGUIView(registry, model=TestModel(), mcp_server=spec_mcp_server)

    (toolset,) = view._run_toolsets(AuthedRequestFactory().post("/agent/"))
    assert set(await toolset.get_tools(None)) == {"invalid", "denied"}


async def test_a_bridged_tool_keeps_its_name_from_the_attachment_toolset() -> None:
    def read_attachment(*, attachment_id: str) -> dict[str, Any]:
        """Read an attachment the project's own way."""
        return {"id": attachment_id}

    bridged = MCPServer(name="attachments")
    bridged.register_tool(
        ServiceSpec(
            service=read_attachment,
            reads=Parameters.of(Parameter("attachment_id", "string", required=True)),
            permissions=[Unrestricted()],
            atomic=False,
        ),
        name="read_attachment",
        description="Read an attachment.",
    )
    view = DjangoAGUIView(
        ToolRegistry(),
        model=TestModel(),
        mcp_server=bridged,
        attachment_store=(
            "django_spec_agents.contrib.store.default_attachment_store.DefaultAttachmentStore"
        ),
    )

    toolsets = view._run_toolsets(AuthedRequestFactory().post("/agent/"))
    assert [type(toolset).__name__ for toolset in toolsets] == ["MCPToolset"]


def test_the_catalog_lists_a_spec_mcp_servers_tools() -> None:
    server = AGUIServer(ToolRegistry(), model=TestModel(), mcp_server=spec_mcp_server)
    tools = _views(server)["tools"]

    response = tools(AuthedRequestFactory().get("/agent/tools/"))

    catalog = {entry["name"]: entry for entry in json.loads(response.content)}
    assert set(catalog) == {"add", "invalid", "denied"}
    assert catalog["add"]["summary"] == "Add numbers"


def test_mcp_server_is_not_deprecated() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        server = AGUIServer(ToolRegistry(), model=TestModel(), mcp_server=spec_mcp_server)
        _views(server)["tools"](AuthedRequestFactory().get("/agent/tools/"))


@pytest.mark.parametrize(
    "build",
    [
        lambda **kw: AGUIServer(ToolRegistry(), model=TestModel(), **kw),
        lambda **kw: DjangoAGUIView(ToolRegistry(), model=TestModel(), **kw),
        lambda **kw: ToolsView(ToolRegistry(), **kw),
    ],
    ids=["AGUIServer", "DjangoAGUIView", "ToolsView"],
)
def test_the_removed_drf_mcp_server_keyword_is_refused(build: Any) -> None:
    # Refused before anything is built, so an old keyword fails at import, not on a request.
    with pytest.raises(TypeError, match=r"unexpected keyword argument 'drf_mcp_server'"):
        build(drf_mcp_server=spec_mcp_server)
