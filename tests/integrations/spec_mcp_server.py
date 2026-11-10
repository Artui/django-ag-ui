from __future__ import annotations

from typing import Any

from django_service_specs import (
    Parameter,
    Parameters,
    ServiceError,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
)
from django_spec_mcp import MCPServer

_TWO_NUMBERS = Parameters.of(
    Parameter("a", "integer", required=True), Parameter("b", "integer", required=True)
)
_OPEN = [Unrestricted()]


def add_numbers(*, a: int, b: int) -> dict[str, Any]:
    """Add two numbers."""
    return {"result": a + b}


def reject_input(*, a: int, b: int) -> dict[str, Any]:
    """Refuse the numbers."""
    raise ServiceValidationError("must be even")


def deny_by_policy(*, a: int, b: int) -> dict[str, Any]:
    """Refuse by a business rule."""
    raise ServiceError("denied by policy")


server = MCPServer(name="test")
server.register_tool(
    ServiceSpec(service=add_numbers, reads=_TWO_NUMBERS, permissions=_OPEN, atomic=False),
    name="add",
    description="Add two numbers.",
    display_name="Add numbers",
)
server.register_tool(
    ServiceSpec(service=reject_input, reads=_TWO_NUMBERS, permissions=_OPEN, atomic=False),
    name="invalid",
    description="Refuse the numbers.",
)
server.register_tool(
    ServiceSpec(service=deny_by_policy, reads=_TWO_NUMBERS, permissions=_OPEN, atomic=False),
    name="denied",
    description="Refuse by a business rule.",
)
