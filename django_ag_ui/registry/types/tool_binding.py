from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_ag_ui.registry.types.tool_spec import ToolSpec


@dataclass(frozen=True)
class ToolBinding:
    """A registered tool plus the JSON Schema derived from its signature.

    The schema is computed once at registration (including the
    ``x-destructive`` / ``x-category`` extensions) and carried alongside
    the spec so tool listings don't re-introspect on every request.
    """

    spec: ToolSpec
    input_schema: dict[str, Any]


__all__ = ["ToolBinding"]
