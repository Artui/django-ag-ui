"""A call the MCP server refuses for permission ends the run.

django-spec-mcp answers a permission check refusing the acting user with
JSON-RPC ``-32006``, and django-spec-agents' bridge raises that as Django's
``PermissionDenied`` from 0.6.2, which the failure policy passes through: the
run ends in ``RUN_ERROR``. Below it the refusal was a failed call the default
``TOOL_FAILURE`` policy redacts, so the model was told the tool failed and the
run finished as though nothing had been refused. This holds the 0.6.2 floor.
"""

from __future__ import annotations

import json
from typing import Any

from django.http import StreamingHttpResponse
from django_service_specs import Parameter, Parameters, PermissionCheck, ServiceSpec
from django_spec_agents.policy.failure.types.tool_failure_config import ToolFailureConfig
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_mcp import MCPServer
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory


class _Refuses(PermissionCheck):
    message = "Insufficient permission"

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


def _server(ran: list[dict[str, int]]) -> MCPServer:
    def add_numbers(*, a: int, b: int) -> dict[str, Any]:
        """Add two numbers."""
        ran.append({"a": a, "b": b})
        return {"result": a + b}

    server = MCPServer(name="refusing")
    server.register_tool(
        ServiceSpec(
            service=add_numbers,
            reads=Parameters.of(
                Parameter("a", "integer", required=True), Parameter("b", "integer", required=True)
            ),
            permissions=[_Refuses()],
            atomic=False,
        ),
        name="add",
        description="Add two numbers.",
    )
    return server


def _calling_add() -> FunctionModel:
    async def stream_fn(messages, info):  # noqa: ANN001, ANN202
        if any(part.part_kind == "tool-return" for part in messages[-1].parts):
            yield "done"
            return
        args = json.dumps({"a": 2, "b": 3})
        yield {0: DeltaToolCall(name="add", json_args=args, tool_call_id="c1")}

    return FunctionModel(stream_function=stream_fn)


async def _events(response: StreamingHttpResponse) -> list[dict[str, Any]]:
    chunks: list[str] = []
    async for chunk in response.streaming_content:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    lines = "".join(chunks).splitlines()
    return [json.loads(line[6:]) for line in lines if line.startswith("data: ")]


async def _run(ran: list[dict[str, int]], **kwargs: Any) -> list[dict[str, Any]]:
    server = AGUIServer(ToolRegistry(), model=_calling_add(), mcp_server=_server(ran), **kwargs)
    patterns, _, _ = server.urls
    endpoint = next(pattern.callback for pattern in patterns if pattern.name == "endpoint")
    body = json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "add 2 and 3"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    )
    return await _events(
        await endpoint(
            AuthedRequestFactory().post("/agent/", data=body, content_type="application/json")
        )
    )


async def test_a_permission_refusal_through_mcp_server_ends_the_run() -> None:
    ran: list[dict[str, int]] = []

    events = await _run(ran)

    types = [event["type"] for event in events]
    # Below 0.6.2 the same call was a redacted failed result the model answered,
    # and the stream closed on ``RUN_FINISHED``.
    assert types[-1] == "RUN_ERROR"
    assert "RUN_FINISHED" not in types
    # Refused before the service ran, not after.
    assert ran == []


async def test_the_run_error_is_the_refusal_when_detail_is_included() -> None:
    # Redacted by default, so the sentence above cannot say which failure ended
    # the run; with the detail opted in it is the server's refusal, by name.
    events = await _run(
        [], config=build_ag_ui_config(tool_failure=ToolFailureConfig(include_detail=True))
    )

    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["message"] == "MCP tool 'add' failed: Insufficient permission"
