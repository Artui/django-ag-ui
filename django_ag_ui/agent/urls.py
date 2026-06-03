from __future__ import annotations

from django.urls import URLPattern, path

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView


def get_urls(
    view: DjangoAGUIView,
    prefix: str = "agent/",
    *,
    skills: SkillRegistry | None = None,
    tools: ToolRegistry | None = None,
) -> list[URLPattern]:
    """Return URL patterns mounting ``view`` at ``<prefix>`` (POST, SSE).

    When ``skills`` is given, also mounts a read-only skills catalog at
    ``<prefix>skills/`` (GET, JSON) for the web component's ``data-skills-url``.

    When ``tools`` (the same :class:`ToolRegistry` the view uses) is given, also
    mounts a read-only **tool catalog** at ``<prefix>tools/`` (GET, JSON) for the
    component's ``data-tools-url`` — friendly card labels for server-side tools.

    Include the result from your project's root URLconf::

        urlpatterns = [
            ...,
            path("", include(get_urls(DjangoAGUIView(registry), tools=registry))),
        ]
    """
    urls = [path(prefix, view, name="django_ag_ui")]
    if skills is not None:
        urls.append(path(f"{prefix}skills/", SkillsView(skills), name="django_ag_ui_skills"))
    if tools is not None:
        urls.append(path(f"{prefix}tools/", ToolsView(tools), name="django_ag_ui_tools"))
    return urls


__all__ = ["get_urls"]
