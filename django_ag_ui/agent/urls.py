from __future__ import annotations

from django.urls import URLPattern, path

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView


def get_urls(
    view: DjangoAGUIView,
    prefix: str = "agent/",
    *,
    skills: SkillRegistry | None = None,
) -> list[URLPattern]:
    """Return URL patterns mounting ``view`` at ``<prefix>`` (POST, SSE).

    When ``skills`` is given, also mounts a read-only skills catalog at
    ``<prefix>skills/`` (GET, JSON) for the web component's ``data-skills-url``.

    Include the result from your project's root URLconf::

        urlpatterns = [
            ...,
            path("", include(get_urls(DjangoAGUIView(registry), skills=skills))),
        ]
    """
    urls = [path(prefix, view, name="django_ag_ui")]
    if skills is not None:
        urls.append(path(f"{prefix}skills/", SkillsView(skills), name="django_ag_ui_skills"))
    return urls


__all__ = ["get_urls"]
