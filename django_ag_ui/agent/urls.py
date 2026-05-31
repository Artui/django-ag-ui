from __future__ import annotations

from django.urls import URLPattern, path

from django_ag_ui.agent.agui_view import DjangoAGUIView


def get_urls(view: DjangoAGUIView, prefix: str = "agent/") -> list[URLPattern]:
    """Return URL patterns mounting ``view`` at ``<prefix>`` (POST, SSE).

    Include the result from your project's root URLconf::

        urlpatterns = [
            ...,
            path("", include(get_urls(DjangoAGUIView(registry)))),
        ]
    """
    return [path(prefix, view, name="django_ag_ui")]


__all__ = ["get_urls"]
