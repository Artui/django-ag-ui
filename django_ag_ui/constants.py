from __future__ import annotations

A2UI_ACTIVITY_TYPE = "a2ui-surface"
"""``activity_type`` of an A2UI surface, the value ``@ag-ui/a2ui-middleware`` emits.

A client that renders A2UI matches on it; one that does not ignores the event.
"""

A2UI_SCHEMA_CONTEXT_DESCRIPTION = (
    "A2UI Component Schema — available components for generating UI surfaces. "
    "Use these component names and properties when creating A2UI operations."
)
"""The ``RunAgentInput.context`` description an A2UI renderer posts its catalog under.

Verbatim from ``@ag-ui/a2ui-middleware``: the entry is found by this exact
string, and its ``value`` is the catalog as JSON (``catalogId``, ``components``
and, for 1.0, ``protocolVersion``).
"""


__all__ = ["A2UI_ACTIVITY_TYPE", "A2UI_SCHEMA_CONTEXT_DESCRIPTION"]
