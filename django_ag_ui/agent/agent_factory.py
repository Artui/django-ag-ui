from __future__ import annotations

from typing import Any, cast

from pydantic_ai import Agent, DeferredToolRequests

from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.policy.audit.audit_capability import AuditCapability
from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger
from django_ag_ui.policy.guard.tool_guard import ToolGuard
from django_ag_ui.registry.tool_registry import ToolRegistry


def build_agent(registry: ToolRegistry, config: AgentConfig) -> Agent[None, Any]:
    """Build a Pydantic-AI ``Agent`` from a registry and an :class:`AgentConfig`.

    Each registry tool is registered as a plain Pydantic-AI tool. When
    ``config.audit_logger`` is set, an :class:`AuditCapability` on the
    ``wrap_tool_execute`` lifecycle hook times and records **every** tool the
    agent runs — registry tools and composed toolsets (drf-mcp / spec /
    attachment / skill tools) alike. Frontend tools declared in the AG-UI
    ``RunAgentInput`` are merged automatically by the adapter and are not
    registered here.

    ``model_settings`` / ``retries`` tune the model; ``toolsets`` and
    ``capabilities`` compose external Pydantic-AI toolsets/capabilities (e.g. an
    MCP-client toolset) alongside the registry tools, so the agent can reach
    beyond the registered set. ``tool_guard``, when enabled, adds a
    :class:`ToolGuard` that flips destructive tools to require approval.

    Capabilities are composed **order-independently**: each declares its
    position via ``get_ordering`` (audit is outermost, the guard is orthogonal),
    and pydantic-ai's ``CombinedCapability`` topologically sorts them — so the
    list built here needn't be pre-ordered.
    """
    capabilities = list(config.capabilities) if config.capabilities is not None else []
    if config.audit_logger is not None and not isinstance(config.audit_logger, NullAuditLogger):
        # Order-independent: AuditCapability.get_ordering() pins it outermost, so
        # it wraps every tool execution regardless of list position.
        capabilities.append(
            AuditCapability(config.audit_logger, ip_address=config.audit_ip_address),
        )
    if config.tool_guard is not None and config.tool_guard.enabled:
        capabilities.append(ToolGuard(registry, config=config.tool_guard))
    return Agent(
        model=config.model,
        tools=[binding.spec.fn for binding in registry],
        # ``[str, DeferredToolRequests]`` turns on the tool-approval interrupt
        # loop for **server-side** tools. The AG-UI adapter only augments
        # ``output_type`` with ``DeferredToolRequests`` when the run carries
        # *frontend* tools (``_adapter.py`` ``if toolset:``), so a run whose only
        # gated tool is server-side would never defer. Setting it here makes the
        # approval path independent of frontend tools; when the adapter also
        # augments, pydantic-ai flattens and dedups the union.
        output_type=[str, DeferredToolRequests],
        instructions=config.instructions,
        # ``model_settings`` is a plain dict at the settings boundary; Agent
        # types it as the ``ModelSettings`` TypedDict.
        model_settings=cast("Any", config.model_settings),
        retries=config.retries,
        toolsets=list(config.toolsets) if config.toolsets is not None else None,
        capabilities=capabilities or None,
    )


__all__ = ["build_agent"]
