"""Dotted-path targets for AGENT_FACTORY / TOOLSETS resolution tests."""

from __future__ import annotations

from typing import Any

from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from django_ag_ui.config.types.ag_ui_config import AGUIConfig


def build_test_agent(registry: ToolRegistry, config: AGUIConfig) -> Agent[None, Any]:  # noqa: ARG001
    """An ``agent_factory=`` escape-hatch target: an agent over ``TestModel``."""
    return build_agent(registry, AgentConfig(model=TestModel()))


# A toolset *instance* — ``resolve_dotted_instances`` should use it as-is.
a_toolset = FunctionToolset()


def make_toolset() -> FunctionToolset:
    """A zero-arg callable returning a toolset — ``resolve_toolsets`` invokes it."""
    return FunctionToolset()
