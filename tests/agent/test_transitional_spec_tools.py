"""Spec objects built from ``rest_framework_pydantic_ai`` are still served.

The ``[spec-tools]`` extra keeps installing ``djangorestframework-pydantic-ai``
for one minor, so a project that built its own ``SpecToolset`` subclass or
``SpecCapability`` from it, and passes that as ``service_specs=``, must keep
working while it moves its imports to ``django_spec_agents.spec_tools``.
``_resolve_spec_source`` recognises each shape by its members rather than by
class, which is what lets these through: a capability by ``get_toolset``, a bare
toolset by a ``specs`` property, the latter wrapped by this package's own
``SpecCapability.from_toolset``. Each test drives a whole run through the
mounted endpoint: the tool is offered to the model, the call is answered, and
the old object's own code is what answered it.

Leaves with the ``[spec-tools]`` extra and the dev dependency backing it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from rest_framework_pydantic_ai import SpecCapability as LegacySpecCapability
from rest_framework_pydantic_ai import SpecToolset as LegacySpecToolset

from django_ag_ui import AGUIServer, ToolRegistry
from tests.authed_request_factory import AuthedRequestFactory
from tests.integrations.drf_specs import SPECS


class _RecordingToolset(LegacySpecToolset):
    """A consumer's subclass of the old toolset, overriding a hook."""

    def __init__(self, specs: Any, *, calls: list[str]) -> None:
        super().__init__(specs)
        self.calls = calls

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any:
        self.calls.append(name)
        return await super().call_tool(name, tool_args, ctx, tool)


def _model(offered: list[list[str]]) -> FunctionModel:
    async def stream_fn(messages: list, info: Any) -> Any:
        offered.append(sorted(t.name for t in info.function_tools))
        answered = any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if answered:
            yield "pinged"
        else:
            yield {0: DeltaToolCall(name="ping", json_args="{}", tool_call_id="call-1")}

    return FunctionModel(stream_function=stream_fn)


async def _run(service_specs: Any, offered: list[list[str]]) -> str:
    server = AGUIServer(ToolRegistry(), model=_model(offered), service_specs=service_specs)
    patterns, _, _ = server.urls
    view = next(p for p in patterns if p.name == "endpoint").callback
    body = json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "ping the server"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()
    request = AuthedRequestFactory().post("/agent/", data=body, content_type="application/json")
    response: StreamingHttpResponse = await view(request)
    chunks = [c if isinstance(c, str) else c.decode() async for c in response.streaming_content]
    return "".join(chunks)


def _result(body: str) -> dict[str, Any]:
    for line in body.splitlines():
        if line.startswith("data:") and '"TOOL_CALL_RESULT"' in line:
            return json.loads(line.removeprefix("data:"))
    raise AssertionError("no TOOL_CALL_RESULT in the stream")


@pytest.mark.django_db
async def test_a_subclass_of_the_old_spec_toolset_is_served() -> None:
    offered: list[list[str]] = []
    calls: list[str] = []

    body = await _run(_RecordingToolset(SPECS, calls=calls), offered)

    assert offered[0] == ["ping"]
    assert calls == ["ping"]
    result = _result(body)
    assert json.loads(result["content"]) == {"ok": True}
    assert result.get("outcome", "success") == "success"
    assert "RUN_FINISHED" in body


@pytest.mark.django_db
async def test_the_old_spec_capability_is_served() -> None:
    offered: list[list[str]] = []
    capability = LegacySpecCapability(SPECS)

    body = await _run(capability, offered)

    assert offered[0] == ["ping"]
    assert json.loads(_result(body)["content"]) == {"ok": True}
    assert "RUN_FINISHED" in body
