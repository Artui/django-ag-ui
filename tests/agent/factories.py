"""Dotted-path targets for AGENT_FACTORY / TOOLSETS resolution tests."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.conf import AppSettings
from django_ag_ui.registry.tool_registry import ToolRegistry


def build_test_agent(registry: ToolRegistry, settings: AppSettings) -> Agent[None, Any]:  # noqa: ARG001
    """An ``AGENT_FACTORY`` escape-hatch target: an agent over ``TestModel``."""
    return build_agent(registry, AgentConfig(model=TestModel()))


# A toolset *instance* — ``resolve_dotted_instances`` should use it as-is.
a_toolset = FunctionToolset()


def make_toolset() -> FunctionToolset:
    """A zero-arg callable returning a toolset — ``resolve_toolsets`` invokes it."""
    return FunctionToolset()
