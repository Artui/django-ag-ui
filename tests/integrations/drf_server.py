"""A fixture drf-mcp ``MCPServer`` with service tools, for bridge tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework.permissions import AllowAny
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.service_spec import ServiceSpec


@dataclass
class AddInput:
    a: int
    b: int


def add_numbers(*, data: AddInput) -> dict[str, Any]:
    return {"result": data.a + data.b}


def reject_input(*, data: AddInput) -> dict[str, Any]:
    raise ServiceValidationError({"a": ["must be even"]})


def deny_by_policy(*, data: AddInput) -> dict[str, Any]:
    raise ServiceError("denied by policy")


# drf-mcp 0.25 refuses to register a tool with no permissions: DRF
# viewset-level and REST_FRAMEWORK defaults do not reach MCP, so an
# omission is an open tool rather than an inherited policy. These are
# bridge fixtures, so AllowAny states "deliberately open" explicitly.
server = MCPServer(name="test")
server.register_service_tool(
    name="add",
    spec=ServiceSpec(permission_classes=[AllowAny], service=add_numbers, input_serializer=AddInput),
)
server.register_service_tool(
    name="invalid",
    spec=ServiceSpec(
        permission_classes=[AllowAny], service=reject_input, input_serializer=AddInput, atomic=False
    ),
)
server.register_service_tool(
    name="denied",
    spec=ServiceSpec(
        permission_classes=[AllowAny],
        service=deny_by_policy,
        input_serializer=AddInput,
        atomic=False,
    ),
)
