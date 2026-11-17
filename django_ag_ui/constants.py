from __future__ import annotations

A2UI_ACTIVITY_TYPE = "a2ui-surface"
"""``activity_type`` of an A2UI surface, the value ``@ag-ui/a2ui-middleware`` emits.

A client that renders A2UI matches on it; one that does not ignores the event.
"""

A2UI_RENDER_TOOL_NAME = "render_a2ui"
"""The name the server's A2UI tool is offered under, the middleware's own."""

DETACH_PROP = "detach"
"""The ``RunAgentInput.forwardedProps`` key by which a client asks its run to outlive it."""

A2UI_SCHEMA_CONTEXT_DESCRIPTION = (
    "A2UI Component Schema — available components for generating UI surfaces. "
    "Use these component names and properties when creating A2UI operations."
)
"""The ``RunAgentInput.context`` description an A2UI renderer posts its catalog under.

Verbatim from ``@ag-ui/a2ui-middleware``: the entry is found by this exact
string, and its ``value`` is the catalog as JSON (``catalogId``, ``components``
and, for 1.0, ``protocolVersion``).
"""

A2UI_RENDER_GUIDE_1_0 = """\
## How to call render_a2ui

You MUST provide ALL required arguments when calling render_a2ui:

- **surfaceId** (string, required): Unique ID for the surface (e.g. "sales-dashboard").
- **components** (array, REQUIRED): A2UI 1.0 flat component array. NEVER omit this.
- **data** (object, optional): Initial data model for path-bound component values.

Note: the catalog id is set by the host, not by you. Do not include a catalogId argument.

### Component format (1.0 flat)

Components are a flat array — children are referenced by ID, not nested:
- Every component has `id` (unique) and `component` (type name from the available catalog).
- The root component MUST have `id: "root"`.
- Properties go directly on the component object.
- Use `children: ["id1", "id2"]` for multiple children, `child: "id"` for a single child.

### Minimal example

```json
{
  "surfaceId": "my-dashboard",
  "components": [
    { "id": "root", "component": "Column", "children": ["title", "row1"] },
    { "id": "title", "component": "Title", "text": "Overview" },
    { "id": "row1", "component": "Row", "children": ["m1", "m2"], "gap": 16 },
    { "id": "m1", "component": "Metric", "label": "Users", "value": "1,200" },
    { "id": "m2", "component": "Metric", "label": "Revenue", "value": "$50K" }
  ]
}
```

### Key rules

1. NEVER call render_a2ui without the `components` array — the UI will be empty.
2. Root must be a layout component (Column, Row, Card) — not Text or Button.
3. Component IDs must be unique. A component must NOT reference itself as child.
4. Only use component names from the Available Components schema in context.
5. For data binding use `{ "@path": "/key" }` (absolute) or `{ "@path": "key" }` (relative inside templates).
   A literal object with a "path" key is NOT a binding in 1.0. Call catalog functions with `{ "@call": "functionName", "args": { ... } }`.
6. For repeating content: `children: { componentId: "card-id", path: "/items" }` repeats per array item.
7. Button actions: `"action": { "event": { "name": "action_name", "context": { ... } } }` — event must be an object.
8. No placeholder images — only use real URLs or Icon components."""  # noqa: E501
"""The usage guide for ``render_a2ui`` under protocol 1.0, ``@ag-ui/a2ui-middleware`` 0.0.16.

Verbatim from ``RENDER_A2UI_TOOL_GUIDELINES("render_a2ui", "1.0")``, delivered as the tool's
instructions, not through the run-context block.
"""

A2UI_RENDER_GUIDE_V0_9 = """\
## How to call render_a2ui

You MUST provide ALL required arguments when calling render_a2ui:

- **surfaceId** (string, required): Unique ID for the surface (e.g. "sales-dashboard").
- **components** (array, REQUIRED): A2UI v0.9 flat component array. NEVER omit this.
- **data** (object, optional): Initial data model for path-bound component values.

Note: the catalog id is set by the host, not by you. Do not include a catalogId argument.

### Component format (v0.9 flat)

Components are a flat array — children are referenced by ID, not nested:
- Every component has `id` (unique) and `component` (type name from the available catalog).
- The root component MUST have `id: "root"`.
- Properties go directly on the component object.
- Use `children: ["id1", "id2"]` for multiple children, `child: "id"` for a single child.

### Minimal example

```json
{
  "surfaceId": "my-dashboard",
  "components": [
    { "id": "root", "component": "Column", "children": ["title", "row1"] },
    { "id": "title", "component": "Title", "text": "Overview" },
    { "id": "row1", "component": "Row", "children": ["m1", "m2"], "gap": 16 },
    { "id": "m1", "component": "Metric", "label": "Users", "value": "1,200" },
    { "id": "m2", "component": "Metric", "label": "Revenue", "value": "$50K" }
  ]
}
```

### Key rules

1. NEVER call render_a2ui without the `components` array — the UI will be empty.
2. Root must be a layout component (Column, Row, Card) — not Text or Button.
3. Component IDs must be unique. A component must NOT reference itself as child.
4. Only use component names from the Available Components schema in context.
5. For data binding use `{ "path": "/key" }` (absolute) or `{ "path": "key" }` (relative inside templates).
6. For repeating content: `children: { componentId: "card-id", path: "/items" }` repeats per array item.
7. Button actions: `"action": { "event": { "name": "action_name", "context": { ... } } }` — event must be an object.
8. No placeholder images — only use real URLs or Icon components."""  # noqa: E501
"""The usage guide for ``render_a2ui`` under protocol v0.9, ``@ag-ui/a2ui-middleware`` 0.0.16.

Verbatim from ``RENDER_A2UI_TOOL_GUIDELINES("render_a2ui", "v0.9")``, delivered as the tool's
instructions, not through the run-context block.
"""


__all__ = [
    "A2UI_ACTIVITY_TYPE",
    "A2UI_RENDER_GUIDE_1_0",
    "A2UI_RENDER_GUIDE_V0_9",
    "A2UI_RENDER_TOOL_NAME",
    "DETACH_PROP",
    "A2UI_SCHEMA_CONTEXT_DESCRIPTION",
]
