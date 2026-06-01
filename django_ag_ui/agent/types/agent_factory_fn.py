from __future__ import annotations

from typing import Any, Protocol

from pydantic_ai import Agent

from django_ag_ui.conf import AppSettings
from django_ag_ui.registry.tool_registry import ToolRegistry


class AgentFactoryFn(Protocol):
    """The escape-hatch signature for ``DJANGO_AG_UI['AGENT_FACTORY']``.

    A dotted path to a callable matching this shape fully replaces the
    built-in :func:`~django_ag_ui.agent.agent_factory.build_agent`, giving a
    project complete control over Pydantic-AI ``Agent`` construction (custom
    model providers, output types, toolsets, instrumentation, …). It receives
    the per-request server-side tool registry and the resolved settings.
    """

    def __call__(self, registry: ToolRegistry, settings: AppSettings) -> Agent[None, Any]: ...


__all__ = ["AgentFactoryFn"]
