from __future__ import annotations

import json
from typing import Any

from django.http import StreamingHttpResponse
from django.test import override_settings
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from django_ag_ui import AGUIServer, ToolRegistry, tool
from tests.authed_request_factory import AuthedRequestFactory


def _model(offered: list[list[str]]) -> FunctionModel:
    async def stream_fn(messages: list, info: Any) -> Any:
        offered.append(sorted(t.name for t in info.function_tools))
        yield {
            0: DeltaToolCall(
                name="remove_widget", json_args='{"target": "widget-1"}', tool_call_id="call-1"
            )
        }

    return FunctionModel(stream_function=stream_fn)


async def _drain(response: StreamingHttpResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.streaming_content:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


def _body() -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "remove widget-1"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


async def test_a_tool_registered_under_its_own_name_is_offered_gated_and_prompted() -> None:
    calls: list[str] = []
    offered: list[list[str]] = []
    registry = ToolRegistry()

    @tool(registry, name="remove_widget", destructive=True, confirm="Remove widget-1?")
    def delete_thing(target: str) -> str:
        """Remove a widget."""
        calls.append(target)
        return f"removed {target}"

    with override_settings(DJANGO_AG_UI={"TOOL_GUARD": {"ENABLED": True}}):
        server = AGUIServer(registry, model=_model(offered))
    patterns, _, _ = server.urls
    view = next(p for p in patterns if p.name == "endpoint").callback
    request = AuthedRequestFactory().post("/agent/", data=_body(), content_type="application/json")

    body = await _drain(await view(request))

    assert offered[0] == ["remove_widget"]
    assert '"type":"interrupt"' in body
    assert '"x-confirm":"Remove widget-1?"' in body
    assert calls == []
