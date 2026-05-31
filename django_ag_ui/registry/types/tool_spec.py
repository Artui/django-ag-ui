from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django_ag_ui.constants import ToolCategory


@dataclass(frozen=True)
class ToolSpec:
    """Canonical declaration of a server-side tool.

    A ``ToolSpec`` bundles the callable with the metadata the registry
    needs to expose it to a Pydantic-AI agent and to a frontend: a
    stable name, a human-facing description, a ``destructive`` risk
    flag, and a coarse ``category``.
    """

    name: str
    """Stable identifier exposed to the agent. Must be unique within a
    :class:`~django_ag_ui.registry.tool_registry.ToolRegistry`."""

    fn: Callable[..., Any]
    """The Python callable that implements the tool. Must have typed
    parameters; the registry derives a JSON Schema from the signature."""

    description: str
    """User-facing summary shown to the agent. The first line is what
    most clients display."""

    destructive: bool = False
    """If ``True``, calling this tool may mutate state. The registry
    stamps ``x-destructive: true`` into the tool's JSON Schema so
    frontends can gate it behind a confirmation step."""

    category: ToolCategory = ToolCategory.OTHER
    """Coarse capability grouping. Surfaced as ``x-category`` in the
    JSON Schema."""


__all__ = ["ToolSpec"]
