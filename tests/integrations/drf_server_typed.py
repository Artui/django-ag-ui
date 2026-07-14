"""A drf-mcp ``MCPServer`` whose tool has an **output serializer**.

Unlike ``drf_server`` (services with no output schema), this fixture's tool
advertises an ``outputSchema`` via ``INCLUDE_OUTPUT_SCHEMA``, so the bridge can
carry a ``return_schema`` onto the tool def — the input a harness ``CodeMode``
capability needs to render the tool as a **typed** Python stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework import serializers
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


@dataclass
class AddInput:
    a: int
    b: int


class SumOutput(serializers.Serializer):
    result = serializers.IntegerField()


def add_numbers(*, data: AddInput) -> dict[str, Any]:
    return {"result": data.a + data.b}


server = MCPServer(name="typed")
server.register_service_tool(
    name="add_typed",
    spec=ServiceSpec(
        service=add_numbers,
        input_serializer=AddInput,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            output_serializer=SumOutput,
        ),
    ),
    permissions=[],
)
