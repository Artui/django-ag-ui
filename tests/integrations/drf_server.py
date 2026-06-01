"""A fixture drf-mcp ``MCPServer`` with one service tool, for bridge tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_services.types.service_spec import ServiceSpec


@dataclass
class AddInput:
    a: int
    b: int


def add_numbers(*, data: AddInput) -> dict[str, Any]:
    return {"result": data.a + data.b}


server = MCPServer(name="test")
server.register_service_tool(
    name="add",
    spec=ServiceSpec(service=add_numbers, input_serializer=AddInput),
)
