"""``AGUIServer(mcp_server=...)``: a django-spec-mcp server bridged into the agent.

Driven end to end through the mounted endpoint and its tool catalog, over a
server registering kernel specs only, so nothing on this path is drf-mcp's. The
deprecated ``drf_mcp_server=`` alias is held beside it: it still bridges a
drf-mcp server, and says which keyword replaces it.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.tools_view import ToolsView
from tests.authed_request_factory import AuthedRequestFactory
from tests.integrations.drf_server import server as drf_server
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
    # What makes the bridge ``MCPToolset`` rather than a duck-typed pass-through:
    # the refusal is a failed call carrying the service's sentence, not a
    # successful result whose payload happens to describe an error.
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


def test_drf_mcp_server_warns_and_names_its_replacement() -> None:
    with pytest.warns(
        DeprecationWarning, match=r"pass a django-spec-mcp server as mcp_server="
    ) as record:
        AGUIServer(ToolRegistry(), model=TestModel(), drf_mcp_server=drf_server)

    # Pointed at the line that passed the old keyword, not at this package.
    (warning,) = [w for w in record if "AGUIServer(drf_mcp_server=)" in str(w.message)]
    assert warning.filename == __file__


async def test_drf_mcp_server_still_bridges_a_drf_mcp_server() -> None:
    from django_spec_agents.integrations.drf_mcp import DRFMCPToolset

    with pytest.warns(DeprecationWarning):
        server = AGUIServer(ToolRegistry(), model=TestModel(), drf_mcp_server=drf_server)
    views = _views(server)

    (toolset,) = views["endpoint"]._run_toolsets(AuthedRequestFactory().post("/agent/"))
    assert isinstance(toolset, DRFMCPToolset)
    catalog = json.loads(views["tools"](AuthedRequestFactory().get("/agent/tools/")).content)
    assert {entry["name"] for entry in catalog} == {"add", "invalid", "denied"}


def test_a_tools_view_built_directly_still_takes_the_old_keyword() -> None:
    # ``AGUIServer`` hands its catalog the server under the new keyword whichever
    # one it was given, so only a ``ToolsView`` built by hand reaches this.
    view = ToolsView(ToolRegistry(), drf_mcp_server=drf_server)

    catalog = json.loads(view(AuthedRequestFactory().get("/agent/tools/")).content)
    assert {entry["name"] for entry in catalog} == {"add", "invalid", "denied"}


@pytest.mark.parametrize(
    "build",
    [
        lambda **kw: AGUIServer(ToolRegistry(), model=TestModel(), **kw),
        lambda **kw: DjangoAGUIView(ToolRegistry(), model=TestModel(), **kw),
        lambda **kw: ToolsView(ToolRegistry(), **kw),
    ],
    ids=["AGUIServer", "DjangoAGUIView", "ToolsView"],
)
def test_both_server_keywords_are_refused(build: Any) -> None:
    with pytest.raises(TypeError, match="takes mcp_server or drf_mcp_server, not both"):
        build(mcp_server=spec_mcp_server, drf_mcp_server=drf_server)
