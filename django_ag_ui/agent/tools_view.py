from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase

from django_ag_ui.agent.build_tool_catalog import build_tool_catalog
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.utils import AuthorizePredicate, auth_error_response, authorize


class ToolsView:
    """A read-only endpoint returning the agent's server-tool catalog (GET, JSON).

    A callable instance (like :class:`~django_ag_ui.agent.agui_view.DjangoAGUIView`)
    holding the same :class:`~django_ag_ui.registry.tool_registry.ToolRegistry`
    the view uses. ``GET`` returns the :func:`build_tool_catalog` list the web
    component fetches via ``data-tools-url`` to label tool-call cards for
    server-side tools (whose schema never reaches the browser).

    The catalog names every server tool the agent can wield — an inventory
    worth gating. The view carries the same authentication seam as
    ``DjangoAGUIView`` (``require_authenticated`` / ``get_user``, sync or
    async hooks), so one policy can cover the agent endpoint and its
    catalogs. Defaults stay open for backwards compatibility — lock the
    catalog down whenever the agent endpoint is locked down.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        require_authenticated: bool = False,
        get_user: Callable[[HttpRequest], Any]
        | Callable[[HttpRequest], Awaitable[Any]]
        | None = None,
        authorize: AuthorizePredicate | None = None,
        drf_mcp_server: Any = None,
        service_specs: dict[str, Any] | None = None,
    ) -> None:
        self._registry = registry
        # The same collaborators the agent view holds, so the catalog lists the
        # tools this endpoint's agent can actually wield — not another's.
        self._drf_mcp_server = drf_mcp_server
        self._service_specs = service_specs
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        deny = authorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        catalog = build_tool_catalog(
            self._registry,
            drf_mcp_server=self._drf_mcp_server,
            service_specs=self._service_specs,
        )
        return JsonResponse(catalog, safe=False)


__all__ = ["ToolsView"]
