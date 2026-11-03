"""A django-spec-mcp ``MCPServer`` whose tool has a **presenter declaring its output**.

Unlike ``spec_mcp_server`` (services with no presenter, so no output schema),
this fixture's tool advertises an ``outputSchema``, so the bridge can carry a
``return_schema`` onto the tool def — the input a harness ``CodeMode``
capability needs to render the tool as a **typed** Python stub.
"""

from __future__ import annotations

from typing import Any

from django_service_specs import (
    Output,
    OutputField,
    Parameter,
    Parameters,
    Presenter,
    ServiceSpec,
    Unrestricted,
)
from django_spec_mcp import MCPServer


class _Sum(Presenter):
    def output(self) -> Output:
        return Output((OutputField("result", "integer"),))

    def present(self, value: Any) -> Any:
        return {"result": value["result"]}


def add_numbers(*, a: int, b: int) -> dict[str, Any]:
    """Add two numbers."""
    return {"result": a + b}


server = MCPServer(name="typed")
server.register_tool(
    ServiceSpec(
        service=add_numbers,
        reads=Parameters.of(
            Parameter("a", "integer", required=True), Parameter("b", "integer", required=True)
        ),
        presenter=_Sum(),
        permissions=[Unrestricted()],
        atomic=False,
    ),
    name="add_typed",
    description="Add two numbers.",
)
