from __future__ import annotations

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase

from django_ag_ui.agent.build_tool_catalog import build_tool_catalog
from django_ag_ui.registry.tool_registry import ToolRegistry


class ToolsView:
    """A read-only endpoint returning the agent's server-tool catalog (GET, JSON).

    A callable instance (like :class:`~django_ag_ui.agent.agui_view.DjangoAGUIView`)
    holding the same :class:`~django_ag_ui.registry.tool_registry.ToolRegistry`
    the view uses. ``GET`` returns the :func:`build_tool_catalog` list the web
    component fetches via ``data-tools-url`` to label tool-call cards for
    server-side tools (whose schema never reaches the browser).
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        return JsonResponse(build_tool_catalog(self._registry), safe=False)


__all__ = ["ToolsView"]
