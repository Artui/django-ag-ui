from __future__ import annotations

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase

from django_ag_ui.skills.skill_registry import SkillRegistry


class SkillsView:
    """A read-only endpoint returning a :class:`SkillRegistry`'s client catalog.

    A callable instance (like :class:`~django_ag_ui.agent.agui_view.DjangoAGUIView`)
    so it can hold the registry and a project can mount several. ``GET`` returns
    the JSON skill list the frontend fetches via ``data-skills-url``.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        return JsonResponse(self._registry.payload(), safe=False)


__all__ = ["SkillsView"]
