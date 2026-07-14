"""The drf-mcp bridge composed with a harness ``CodeMode`` capability.

Two halves:

- The **bridge** carries drf-mcp's ``outputSchema`` onto ``ToolDefinition.return_schema``
  (no harness needed) — the input CodeMode reads to type its stubs.
- The **recipe**: with ``CodeMode`` in the agent's capabilities, the bridged tools
  collapse into one sandboxed ``run_code`` tool, and a tool that carries a return
  schema renders as a **typed** stub (no ``-> Any`` warning) while one that doesn't
  warns — which is exactly why the bridge propagates the schema.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import RequestFactory
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.integrations.drf_mcp import DRFMCPToolset
from django_ag_ui.registry.tool_registry import ToolRegistry
from tests.integrations.drf_server import server as untyped_server
from tests.integrations.drf_server_typed import server as typed_server


def _request() -> HttpRequest:
    request = RequestFactory().post("/agent/")
    request.user = AnonymousUser()  # type: ignore[attr-defined]
    return request


# --- bridge: outputSchema → return_schema (no harness) ------------------------------


def test_bridge_carries_output_schema_as_return_schema() -> None:
    defs = {d.name: d for d in DRFMCPToolset(typed_server, _request())._load_tool_defs()}
    add = defs["add_typed"]
    assert add.return_schema == {
        "type": "object",
        "properties": {"result": {"type": "integer"}},
        "required": ["result"],
    }
    assert add.include_return_schema is True


def test_bridge_omits_return_schema_when_no_output_schema() -> None:
    # A service with no output serializer advertises no ``outputSchema``, so the
    # def carries none — the stub falls back to ``-> Any``.
    defs = {d.name: d for d in DRFMCPToolset(untyped_server, _request())._load_tool_defs()}
    add = defs["add"]
    assert add.return_schema is None
    assert add.include_return_schema is False


# --- recipe: CodeMode wraps the bridge into run_code (needs the [harness] extra) -----


async def _compose_with_code_mode(server: Any) -> tuple[list[str], list[str]]:
    """Build ``build_agent`` + a drf-mcp toolset + ``CodeMode``, run once, and
    return (tool names the model saw, CodeMode's ``-> Any`` warnings)."""
    # The [harness] extra (with its [code-mode] sandbox) is a dev dependency, so
    # this runs in CI; importorskip only guards a stripped-down environment.
    code_mode = pytest.importorskip("pydantic_ai_harness.code_mode")

    seen: dict[str, list[str]] = {}

    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        seen["tools"] = [tool.name for tool in info.function_tools]
        return ModelResponse(parts=[TextPart(content="done")])

    agent = build_agent(
        ToolRegistry(),
        AgentConfig(
            model=FunctionModel(model_fn),
            toolsets=[DRFMCPToolset(server, _request())],
            capabilities=[code_mode.CodeMode()],
        ),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await agent.run("do the thing")
    any_warnings = [str(w.message) for w in caught if "-> Any" in str(w.message)]
    return seen.get("tools", []), any_warnings


async def test_code_mode_collapses_typed_drf_mcp_tools_into_run_code() -> None:
    tools, any_warnings = await _compose_with_code_mode(typed_server)
    # The bridged tool is wrapped: the model sees only ``run_code``, not ``add_typed``.
    assert "run_code" in tools
    assert "add_typed" not in tools
    # It carries a return schema → a typed stub, so CodeMode does not warn.
    assert any_warnings == []


async def test_code_mode_warns_for_a_tool_without_a_return_schema() -> None:
    # The same composition over services with no output schema still batches, but
    # CodeMode warns that the stubs are untyped — the contrast that shows the
    # bridge's return_schema propagation is what earns the typed stub above.
    tools, any_warnings = await _compose_with_code_mode(untyped_server)
    assert tools == ["run_code"]
    assert any("add" in w for w in any_warnings)
