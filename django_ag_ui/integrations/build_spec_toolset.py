"""Build a Pydantic-AI toolset over drf-services specs — no MCP hop.

Requires the ``django-ag-ui[spec-tools]`` extra. Imported lazily by the view
only when ``DJANGO_AG_UI["SERVICE_SPECS"]`` is set, so the dependency on
``djangorestframework-pydantic-ai`` (and drf-services) stays optional.

Unlike :mod:`~django_ag_ui.integrations.drf_mcp`, there is no MCP server in the
path: ``djangorestframework-pydantic-ai``'s ``SpecToolset`` calls the specs in
process through drf-services' transport-neutral surface (`dispatch_spec` + its
off-HTTP helpers), enforcing each spec's ``permission_classes``. The agent acts
as the **logged-in AG-UI user**: the user is bound from ``request`` here (rather
than read off ``RunContext.deps``), matching how :class:`DRFMCPToolset` carries
the request, so it drops into the same per-request ``AgentConfig.toolsets`` seam.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest


def build_spec_toolset(
    specs: dict[str, Any],
    request: HttpRequest,
    *,
    exclude_names: frozenset[str] = frozenset(),
) -> Any:
    """A ``SpecToolset`` over ``specs``, acting as ``request.user``.

    ``specs`` is a ``name -> ServiceSpec/SelectorSpec`` mapping (resolved from
    ``DJANGO_AG_UI["SERVICE_SPECS"]``). Names in ``exclude_names`` — the ``@tool``
    registry's names — are dropped so a registry tool wins a collision (the same
    rule the drf-mcp bridge and ``build_tool_catalog`` apply); otherwise
    pydantic-ai raises ``UserError`` for the duplicate name at run time. The
    ``get_user`` hook ignores ``ctx.deps`` and returns the request's user, which
    the view has already materialized off the event loop.
    """
    from rest_framework_pydantic_ai import SpecToolset

    selected = {name: spec for name, spec in specs.items() if name not in exclude_names}
    return SpecToolset(selected, get_user=lambda _ctx: request.user)


__all__ = ["build_spec_toolset"]
