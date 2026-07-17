from __future__ import annotations

from typing import Any, Protocol

from pydantic_ai import Agent

from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.registry.tool_registry import ToolRegistry


class AgentFactoryFn(Protocol):
    """The escape-hatch signature for ``AGUIServer(agent_factory=...)``.

    A callable matching this shape fully replaces the built-in
    :func:`~django_ag_ui.agent.agent_factory.build_agent`, giving a project
    complete control over Pydantic-AI ``Agent`` construction (custom model
    providers, output types, toolsets, instrumentation, …). It receives the
    server-side tool registry and that endpoint's resolved
    :class:`~django_ag_ui.config.types.ag_ui_config.AGUIConfig`.
    """

    def __call__(self, registry: ToolRegistry, config: AGUIConfig) -> Agent[None, Any]: ...


__all__ = ["AgentFactoryFn"]
