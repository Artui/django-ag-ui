"""Build a Pydantic-AI capability over drf-services specs — no MCP hop.

Requires the ``django-ag-ui[spec-tools]`` extra. Imported lazily by the view
only when ``DJANGO_AG_UI["SERVICE_SPECS"]`` is set, so the dependency on
``djangorestframework-pydantic-ai`` (and drf-services) stays optional.

Unlike :mod:`~django_ag_ui.integrations.drf_mcp`, there is no MCP server in the
path: ``djangorestframework-pydantic-ai``'s ``SpecCapability`` wraps a
``SpecToolset`` that calls the specs in process through drf-services'
transport-neutral surface (`dispatch_spec` + its off-HTTP helpers), enforcing
each spec's ``permission_classes``. The agent acts as the **logged-in AG-UI
user**: the user is bound from ``request`` here (rather than read off
``RunContext.deps``), matching how :class:`DRFMCPToolset` carries the request.

Choosing the *capability* over the bare ``SpecToolset`` is deliberate: the
exposed tool set is byte-identical, but the capability also teaches the model
``SpecToolset``'s conventions (``page`` / ``limit`` / ``order`` on list tools, and
the error contract — an ``{"error": …}`` result is a final answer, a retry
message means fix the argument, a permission error is final) through
``get_instructions()``, which pydantic-ai appends to the system prompt each turn.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest


def build_spec_capability(
    specs: dict[str, Any],
    request: HttpRequest,
    *,
    exclude_names: frozenset[str] = frozenset(),
) -> Any:
    """A ``SpecCapability`` over ``specs``, acting as ``request.user``.

    ``specs`` is a ``name -> ServiceSpec/SelectorSpec`` mapping (resolved from
    ``DJANGO_AG_UI["SERVICE_SPECS"]``). Names in ``exclude_names`` — the names a
    higher-precedence source (the ``@tool`` registry, the drf-mcp bridge) already
    claimed — are dropped so that source wins a collision (the same rule
    ``build_tool_catalog`` applies); otherwise pydantic-ai raises ``UserError``
    for the duplicate name at run time. The ``get_user`` hook ignores
    ``ctx.deps`` and returns the request's user, which the view has already
    materialized off the event loop.
    """
    from rest_framework_pydantic_ai import SpecCapability

    selected = {name: spec for name, spec in specs.items() if name not in exclude_names}
    return SpecCapability(selected, get_user=lambda _ctx: request.user)


__all__ = ["build_spec_capability"]
