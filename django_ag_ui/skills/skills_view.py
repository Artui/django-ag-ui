from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase

from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.utils import authorize


class SkillsView:
    """A read-only endpoint returning a :class:`SkillRegistry`'s client catalog.

    A callable instance (like :class:`~django_ag_ui.agent.agui_view.DjangoAGUIView`)
    so it can hold the registry and a project can mount several. ``GET`` returns
    the JSON skill list the frontend fetches via ``data-skills-url``.

    Skill prompts can encode internal workflows — worth gating. The view
    carries the same authentication seam as ``DjangoAGUIView``
    (``require_authenticated`` / ``get_user``, sync or async hooks), so one
    policy can cover the agent endpoint and its catalogs. Defaults stay open
    for backwards compatibility — lock the catalog down whenever the agent
    endpoint is locked down.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        require_authenticated: bool = False,
        get_user: Callable[[HttpRequest], Any]
        | Callable[[HttpRequest], Awaitable[Any]]
        | None = None,
    ) -> None:
        self._registry = registry
        self._require_authenticated = require_authenticated
        self._get_user = get_user

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        if not authorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
        ):
            return JsonResponse({"error": "authentication required"}, status=401)
        return JsonResponse(self._registry.payload(), safe=False)


__all__ = ["SkillsView"]
