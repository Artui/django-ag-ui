from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from django.http import HttpRequest
from django_spec_agents import DESTRUCTIVE_METADATA_KEY
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_agents.routines import ROUTINE_AUTHORING_TOOLS, AbstractRoutine
from pydantic_ai.tools import ToolDefinition

from django_ag_ui.agent.publish_invalidation import INVALIDATION_SINK
from django_ag_ui.agent.resource_invalidation import ROUTINES_KEY, resource_invalidation


def grantable_tool_defs(
    registry: ToolRegistry,
    request: HttpRequest,
    *,
    mcp_server: Any = None,
    service_specs: Mapping[str, Any] | None = None,
) -> list[ToolDefinition]:
    """The mount's server tools a routine may be granted, from the sources a run's catalog has."""
    defs = [
        ToolDefinition(name=binding.spec.name, parameters_json_schema=binding.input_schema)
        for binding in registry
    ]
    if mcp_server is not None:
        defs.extend(_mcp_tool_defs(mcp_server, request))
    if service_specs:
        defs.extend(_spec_tool_defs(service_specs))
    seen: set[str] = set()
    unique = []
    for tool_def in defs:
        if tool_def.name not in seen and tool_def.name not in ROUTINE_AUTHORING_TOOLS:
            seen.add(tool_def.name)
            unique.append(tool_def)
    return unique


def _mcp_tool_defs(server: Any, request: HttpRequest) -> list[ToolDefinition]:
    """Every tool the bridged server lists for the caller, marked destructive as the bridge does."""
    defs: list[ToolDefinition] = []
    cursor: str | None = None
    while True:
        payload = server.list_tools(
            cursor, user=request.user, request=request, include_unavailable=True
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"MCP tools/list failed: {payload.message}")
        for tool in payload["tools"]:
            annotations = tool.get("annotations") or {}
            mutates = (
                bool(annotations)
                and annotations.get("readOnlyHint") is not True
                and annotations.get("destructiveHint") is not False
            )
            metadata = {DESTRUCTIVE_METADATA_KEY: True} if mutates else None
            defs.append(
                ToolDefinition(
                    name=tool["name"],
                    parameters_json_schema=tool["inputSchema"],
                    metadata=metadata,
                )
            )
        cursor = payload.get("nextCursor")
        if cursor is None:
            return defs


def _spec_tool_defs(specs: Mapping[str, Any]) -> list[ToolDefinition]:
    """One definition per spec, a selector read-only and a service not, as the spec toolset says."""
    from django_service_specs import SelectorSpec, kernel_spec

    return [
        ToolDefinition(
            name=name,
            metadata={"annotations": {"readOnlyHint": isinstance(kernel_spec(spec), SelectorSpec)}},
        )
        for name, spec in specs.items()
    ]


def announce_routine_saved(sender: Any, instance: AbstractRoutine, **kwargs: Any) -> None:
    """Queue the routines invalidation on the run streaming in this context, once committed."""
    sink = INVALIDATION_SINK.get()
    if sink is None:
        return
    event = resource_invalidation(
        ROUTINES_KEY, f"{ROUTINES_KEY}/{instance.pk}", reason="routine_saved"
    )
    transaction.on_commit(lambda: sink.append(event))
