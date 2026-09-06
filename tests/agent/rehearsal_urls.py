"""A root URLconf that mounts an :class:`AGUIServer` carrying **no model**.

The documented rehearsal recipe: ``DJANGO_AG_UI["MODEL"] = "test"`` in settings,
nothing passed to the constructor. Every other mount in this suite hands the view
a ``TestModel()`` instance directly, which answers a different question -- it
proves the view streams, while skipping the settings read that a real deployment
depends on.

Constructed at import, which is when a real ``urls.py`` constructs one. The
importer is ``test_rehearsing_without_a_provider_key``, whose
``override_settings`` is in force at the point Django first resolves against this
module; a stray import from anywhere else finds no ``MODEL`` at all and raises
``ImproperlyConfigured``, so the failure mode is loud rather than a server built
against the wrong settings.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.urls import path
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry

from django_ag_ui.agent.agui_server import AGUIServer
from tests.authed_request_factory import authenticated_user

_registry = ToolRegistry()


@tool(_registry)
def double(n: int) -> int:
    """Double a number."""
    return n * 2


def _get_user(_request: HttpRequest) -> Any:
    """Stands in for the token lookup the quickstart documents.

    A hook rather than auth middleware because the suite's settings mount none;
    what matters is that the gate resolves a user the way a deployment's does,
    not where the user came from.
    """
    return authenticated_user()


_server = AGUIServer(_registry, get_user=_get_user, csrf_exempt=True)

urlpatterns = [path("agent/", _server.urls)]
