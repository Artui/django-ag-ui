"""A harness ``SubAgents`` capability wired through the ``CAPABILITIES`` seam.

The recipe: with ``SubAgents`` in the agent's capabilities, the parent agent
gains a single ``delegate_task`` tool (name configurable) and the child agents
are *not* exposed as direct tools — the parent reaches them only by delegating.
This mirrors the CodeMode recipe: a stateless harness capability adopted with no
django-ag-ui package code, just the ``[harness]`` extra + this composition.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.registry.tool_registry import ToolRegistry


async def _parent_tool_names_with_subagents(tool_name: str = "delegate_task") -> list[str]:
    """Compose ``build_agent`` + a ``SubAgents`` capability, run once, and return
    the tool names the parent model saw."""
    # The [harness] extra is a dev dependency, so this runs in CI; importorskip
    # only guards a stripped-down environment. ``subagents`` ships in the base
    # ``pydantic-ai-harness`` package (not behind the [code-mode] sandbox extra).
    subagents = pytest.importorskip("pydantic_ai_harness.subagents")

    # A child agent that never calls the model in this test — it exists only so
    # ``SubAgents`` exposes ``delegate_task``. ``agent_folders=None`` keeps the
    # capability from auto-loading Markdown agents off disk.
    child = Agent(
        FunctionModel(lambda m, i: ModelResponse(parts=[TextPart(content="ok")])), name="researcher"
    )

    seen: dict[str, list[str]] = {}

    def parent_fn(messages: list, info: AgentInfo) -> ModelResponse:
        seen["tools"] = [tool.name for tool in info.function_tools]
        return ModelResponse(parts=[TextPart(content="done")])

    agent = build_agent(
        ToolRegistry(),
        AgentConfig(
            model=FunctionModel(parent_fn),
            capabilities=[
                subagents.SubAgents(
                    agents=[subagents.SubAgent(child, description="Researches a topic.")],
                    agent_folders=None,
                    tool_name=tool_name,
                )
            ],
        ),
    )
    await agent.run("do the thing")
    return seen.get("tools", [])


async def test_subagents_exposes_the_delegate_tool() -> None:
    tools = await _parent_tool_names_with_subagents()
    # The parent sees the single delegation tool, not the child agent directly.
    assert "delegate_task" in tools
    assert "researcher" not in tools


async def test_subagents_delegate_tool_name_is_configurable() -> None:
    tools = await _parent_tool_names_with_subagents(tool_name="ask_specialist")
    assert "ask_specialist" in tools
    assert "delegate_task" not in tools
