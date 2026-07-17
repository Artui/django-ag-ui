from __future__ import annotations

import inspect
import re
from typing import Any

from django_ag_ui.registry.tool_registry import ToolRegistry


def build_tool_catalog(
    registry: ToolRegistry,
    *,
    drf_mcp_server: Any = None,
    service_specs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The agent's server-tool catalog for the frontend to label tool-call cards.

    Server-side tools (the ``@tool`` registry, and drf-mcp tools when a
    ``drf_mcp_server`` is passed) execute server-side, so their
    JSON Schema never reaches the browser — the web component can't read an
    ``x-summary`` off it. This catalog is the channel for those labels: the
    component fetches it via ``data-tools-url`` and maps tool name → label.

    Each entry is ``{"name", "summary", "description"?}``. ``summary`` is always
    present, resolved from the single source of truth with a fallback chain:

    - registry tools → ``@tool(summary=…)`` → a prettified name;
    - drf-mcp tools → ``display_name`` → ``title`` → a prettified name.

    ``description`` (a longer blurb for tooltips) is included when available
    (``ToolSpec.description`` / drf-mcp ``display_description`` → ``description``).
    Registry tools win on name collisions.
    """
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for binding in registry:
        spec = binding.spec
        catalog.append(_entry(spec.name, spec.summary, spec.description))
        seen.add(spec.name)
    if drf_mcp_server is not None:
        for binding in drf_mcp_server.tools.all():
            if binding.name in seen:
                continue
            summary = getattr(binding, "display_name", None) or getattr(binding, "title", None)
            description = getattr(binding, "display_description", None) or binding.description
            catalog.append(_entry(binding.name, summary, description))
            seen.add(binding.name)
    if service_specs is not None:
        for name, spec in service_specs.items():
            if name in seen:
                continue
            catalog.append(_entry(name, None, _spec_description(spec)))
            seen.add(name)
    return catalog


def _spec_description(spec: Any) -> str | None:
    """A spec tool's blurb: the docstring of its service / selector callable.

    Read via ``getattr`` (not isinstance) so this never imports the drf-services
    spec classes — the ``[spec-tools]`` extra need not be installed to fetch the
    catalog when ``SERVICE_SPECS`` is unset.
    """
    callable_ = getattr(spec, "service", None) or getattr(spec, "selector", None)
    return inspect.getdoc(callable_) if callable_ is not None else None


def _entry(name: str, summary: str | None, description: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "summary": summary or _prettify(name)}
    if description:
        entry["description"] = description
    return entry


def _prettify(name: str) -> str:
    """Fallback label from a tool name: ``query_model`` → ``"Query model"``."""
    text = " ".join(word for word in re.split(r"[_\-\s]+", name) if word)
    return text[:1].upper() + text[1:]


__all__ = ["build_tool_catalog"]
