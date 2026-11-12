from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.agent.build_tool_catalog import build_tool_catalog
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_agents.utils import AuthorizePredicate, auth_error_response, authorize

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt


class ToolsView:
    """A read-only endpoint returning the agent's server-tool catalog (GET, JSON).

    A callable instance (like [`DjangoAGUIView`][django_ag_ui.DjangoAGUIView])
    holding the same [`ToolRegistry`][django_ag_ui.ToolRegistry]
    the view uses. ``GET`` returns the
    [`build_tool_catalog`][django_spec_agents.agent.build_tool_catalog.build_tool_catalog]
    list the web
    component fetches via ``data-tools-url`` to label tool-call cards for
    server-side tools (whose schema never reaches the browser).

    The catalog names every server tool the agent can wield — an inventory worth
    gating — so the view carries the same authentication seam as
    ``DjangoAGUIView`` (``require_authenticated`` / ``get_user``, sync or async)
    and the same closed default: an anonymous ``GET`` is a ``401`` until you pass
    ``require_authenticated=False``.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        require_authenticated: bool = True,
        get_user: Callable[[HttpRequest], Any]
        | Callable[[HttpRequest], Awaitable[Any]]
        | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
        mcp_server: Any = None,
        service_specs: dict[str, Any] | None = None,
        spec_capability: Any = None,
    ) -> None:
        self._registry = registry
        # The same collaborators the agent view holds, so the catalog matches the agent.
        self._mcp_server = mcp_server
        self._service_specs = service_specs
        self._spec_capability = spec_capability
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        # Authorize before the method check, so a mounted endpoint looks like an absent one.
        deny = authorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        catalog = build_tool_catalog(
            self._registry,
            mcp_server=self._mcp_server,
            service_specs=self._service_specs,
        )
        return JsonResponse(catalog, safe=False)


__all__ = ["ToolsView"]
