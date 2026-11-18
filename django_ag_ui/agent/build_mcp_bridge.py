from __future__ import annotations

from collections.abc import Set
from typing import Any

from django.http import HttpRequest


def build_mcp_bridge(server: Any, request: HttpRequest, *, exclude_names: Set[str]) -> Any:
    """The request-bound MCP bridge (id ``"mcp"``) a run and the ``mcp-apps/`` route share."""
    from django_spec_agents.integrations.mcp import MCPToolset

    return MCPToolset(server, request, exclude_names=frozenset(exclude_names))


__all__ = ["build_mcp_bridge"]
