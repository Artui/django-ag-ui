from __future__ import annotations

import json
import re
from typing import Any

from pydantic_ai import ModelRetry, RunContext, Tool, ToolReturn
from pydantic_ai.toolsets.function import FunctionToolset

from django_ag_ui.agent.a2ui_activity import a2ui_activity
from django_ag_ui.agent.utils import a2ui_validator
from django_ag_ui.constants import A2UI_SCHEMA_CONTEXT_DESCRIPTION

_V0_9 = "v0.9"
_V1_0 = "1.0"
_DECLARED_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.\d+)?(?:[-+].*)?", re.IGNORECASE | re.ASCII)
_LITERALS = ("literalString", "literalNumber", "literalBoolean")
_V09_UNDER_V1 = (
    'Components use A2UI v0.9 bindings ({"path": ...} or literal* values) but the catalog '
    'declares protocol 1.0; bind values with {"@path": ...}'
)
_V1_UNDER_V09 = (
    'Components use A2UI 1.0 directives ("@path"/"@call") but the catalog declares protocol '
    'v0.9; bind values with {"path": ...}'
)


def build_a2ui_toolset(run_input: Any) -> FunctionToolset[Any] | None:
    """A ``render_a2ui`` tool for this run's posted catalog, or ``None`` without one."""
    catalog = _posted_catalog(run_input)
    if catalog is None:
        return None
    validate = a2ui_validator()
    protocol = _normalize_protocol_version(catalog.get("protocolVersion")) or _V0_9
    label = "1.0" if protocol == _V1_0 else "v0.9"

    async def render_a2ui(
        ctx: RunContext[Any], surfaceId: Any = None, components: Any = None, data: Any = None
    ) -> ToolReturn:
        problems = _argument_problems(surfaceId, data)
        if not problems:
            known = {"components": catalog["components"]} if catalog["components"] else None
            result = validate(components=components, data=data, catalog=known)
            problems = [f"{error['path']}: {error['message']}" for error in result["errors"]]
        if not problems:
            problems = _protocol_mismatch(protocol, components)
        if problems:
            raise ModelRetry("\n".join(problems))
        operations = _operations(protocol, surfaceId, catalog["catalogId"], components, data)
        return ToolReturn(
            return_value=f"Surface {surfaceId!r} rendered.",
            metadata=[a2ui_activity(operations, message_id=f"a2ui-surface-{ctx.tool_call_id}")],
        )

    tool = Tool.from_schema(
        render_a2ui,
        name="render_a2ui",
        description=f"Render a dynamic A2UI {label} surface with structured parameters. "
        "Follow the A2UI render tool usage guide provided in context.",
        json_schema=_parameters(label),
        takes_ctx=True,
    )
    return FunctionToolset([tool], id="django-ag-ui-a2ui")


def _posted_catalog(run_input: Any) -> dict[str, Any] | None:
    """The first A2UI schema entry's catalog, when it carries an id and components."""
    entry = next(
        (e for e in run_input.context if e.description == A2UI_SCHEMA_CONTEXT_DESCRIPTION), None
    )
    if entry is None:
        return None
    try:
        catalog = json.loads(entry.value)
    except (ValueError, RecursionError):
        return None
    if not isinstance(catalog, dict):
        return None
    catalog_id = catalog.get("catalogId")
    if not isinstance(catalog_id, str) or not catalog_id:
        return None
    return catalog if isinstance(catalog.get("components"), dict) else None


def _normalize_protocol_version(value: Any) -> str | None:
    """A declared version's wire label, read as the middleware reads it, or ``None``."""
    if not isinstance(value, str):
        return None
    match = _DECLARED_VERSION.fullmatch(value.strip())
    if match is None:
        return None
    return {(0, 9): _V0_9, (1, 0): _V1_0}.get((int(match[1]), int(match[2])))


def _argument_problems(surface_id: Any, data: Any) -> list[str]:
    """What the toolkit does not check: a usable surface id and an object for ``data``."""
    problems: list[str] = []
    if not isinstance(surface_id, str) or not surface_id.strip():
        problems.append("surfaceId: must be a non-empty string")
    if data is not None and not isinstance(data, dict):
        problems.append("data: must be an object")
    return problems


def _protocol_markers(components: list[Any]) -> tuple[int, int]:
    """Counts of v0.9 bindings and 1.0 directives, as ``detectProtocolMarkers`` counts them."""
    counts = [0, 0]

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if "@path" in node or "@call" in node:
            counts[1] += 1
        elif _is_v09_binding(node):
            counts[0] += 1
        for value in node.values():
            visit(value)

    for component in components:
        for key, value in component.items():
            if key not in ("id", "component"):
                visit(value)
    return counts[0], counts[1]


def _is_v09_binding(node: dict[str, Any]) -> bool:
    """A ``{"path"}`` binding that is not a repeat template, or a ``literal*`` value."""
    bound = isinstance(node.get("path"), str) and not isinstance(node.get("componentId"), str)
    return bound or any(literal in node for literal in _LITERALS)


def _protocol_mismatch(protocol: str, components: list[Any]) -> list[str]:
    """The ``protocolMismatchErrors`` rule: bindings must be spelled as the catalog declares."""
    v09, v1 = _protocol_markers(components)
    if protocol == _V1_0 and v09 > 0 and v1 == 0:
        return [f"components: {_V09_UNDER_V1}"]
    if protocol == _V0_9 and v1 > 0:
        return [f"components: {_V1_UNDER_V09}"]
    return []


def _operations(
    protocol: str, surface_id: str, catalog_id: str, components: list[Any], data: Any
) -> list[dict[str, Any]]:
    """The middleware's operation list for one surface, stamped with the catalog's protocol."""
    operations: list[dict[str, Any]] = [
        {"version": protocol, "createSurface": {"surfaceId": surface_id, "catalogId": catalog_id}},
        {
            "version": protocol,
            "updateComponents": {"surfaceId": surface_id, "components": components},
        },
    ]
    if data is not None:
        operations.append(
            {
                "version": protocol,
                "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data},
            }
        )
    return operations


def _parameters(label: str) -> dict[str, Any]:
    """The ``renderA2UITool`` parameter schema, worded for the catalog's protocol."""
    return {
        "type": "object",
        "properties": {
            "surfaceId": {"type": "string", "description": "Unique surface identifier."},
            "components": {
                "type": "array",
                "description": f"A2UI {label} component array (flat format). "
                'The root component must have id "root".',
                "items": {"type": "object"},
            },
            "data": {
                "type": "object",
                "description": "Initial data model for the surface. Written to the root path. "
                'Use for pre-filling form values (e.g. {"form": {"name": "Alice"}}) '
                "or providing data for components bound to data model paths.",
            },
        },
        "required": ["surfaceId", "components"],
    }


__all__ = ["build_a2ui_toolset"]
