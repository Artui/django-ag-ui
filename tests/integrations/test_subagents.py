from __future__ import annotations

import pytest
from django_spec_agents.agent.agent_factory import build_agent
from django_spec_agents.agent.types.agent_config import AgentConfig
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def _parent_tool_names_with_subagents(tool_name: str = "delegate_task") -> list[str]:
    """Builds build_agent with SubAgents, runs once, and returns the tool names the parent saw."""
    subagents = pytest.importorskip("pydantic_ai_harness.subagents")

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
