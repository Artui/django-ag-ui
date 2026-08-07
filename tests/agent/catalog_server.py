"""A drf-mcp ``MCPServer`` fixture exercising the tool-catalog label fallbacks.

Three tools cover the ``display_name → title → prettified name`` summary chain
and the ``display_description → description`` description chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework.permissions import AllowAny
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_services.types.service_spec import ServiceSpec


@dataclass
class _Args:
    x: int


def _svc(*, data: _Args) -> dict[str, Any]:
    return {"x": data.x}


# drf-mcp 0.25 refuses to register a tool with no permissions: DRF
# viewset-level and REST_FRAMEWORK defaults do not reach MCP, so an
# omission is an open tool rather than an inherited policy. These are
# bridge fixtures, so AllowAny states "deliberately open" explicitly.
server = MCPServer(name="catalog-test")
# display_name / display_description → consumer-only label + blurb.
server.register_service_tool(
    name="ping",
    spec=ServiceSpec(permission_classes=[AllowAny], service=_svc, input_serializer=_Args),
    display_name="Ping the service",
    display_description="Health check.",
)
# title + description, no display_* → summary falls back to title, description
# to the protocol description.
server.register_service_tool(
    name="lookup_widget",
    spec=ServiceSpec(permission_classes=[AllowAny], service=_svc, input_serializer=_Args),
    title="Lookup widget",
    description="Find a widget.",
)
# Nothing → summary prettified from the name, no description.
server.register_service_tool(
    name="raw_tool",
    spec=ServiceSpec(permission_classes=[AllowAny], service=_svc, input_serializer=_Args),
)
