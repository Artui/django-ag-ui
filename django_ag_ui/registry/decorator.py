from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from django_ag_ui.constants import ToolCategory
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.registry.types.tool_spec import ToolSpec

F = TypeVar("F", bound=Callable[..., Any])


def tool(
    registry: ToolRegistry,
    *,
    name: str | None = None,
    description: str | None = None,
    destructive: bool = False,
    category: ToolCategory = ToolCategory.OTHER,
    confirm: str | None = None,
    summary: str | None = None,
) -> Callable[[F], F]:
    """Register the decorated callable on ``registry`` as a tool.

    The tool's name defaults to the function name; its description
    defaults to the first paragraph of the function's docstring. Both
    can be overridden via the keyword arguments. ``confirm`` supplies a
    human-readable confirmation prompt for a destructive tool (surfaced as
    ``x-confirm``); ``summary`` a short display label (surfaced as
    ``x-summary``).
    """

    def _decorator(fn: F) -> F:
        spec = ToolSpec(
            name=name or getattr(fn, "__name__", "<anonymous>"),
            fn=fn,
            description=description if description is not None else _first_doc(fn),
            destructive=destructive,
            category=category,
            confirm=confirm,
            summary=summary,
        )
        registry.register(spec)
        return fn

    return _decorator


def _first_doc(fn: Callable[..., Any]) -> str:
    doc = inspect.getdoc(fn)
    if not doc:
        return ""
    paragraph: list[str] = []
    for line in doc.splitlines():
        if not line.strip():
            break
        paragraph.append(line)
    return " ".join(paragraph).strip()


__all__ = ["tool"]
