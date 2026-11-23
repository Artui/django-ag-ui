from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.http import HttpRequest
from django.urls import path
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agents_directory import AgentsDirectory
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.skills.skill_registry import SkillRegistry
from tests.authed_request_factory import authenticated_user

TOKEN = "Bearer token-two"


def token_user(request: HttpRequest) -> Any:
    if request.headers.get("Authorization") == TOKEN:
        return authenticated_user(pk=2, username="token")
    return SimpleNamespace(is_authenticated=False, pk=None)


def _server(**kwargs: Any) -> AGUIServer:
    return AGUIServer(ToolRegistry(), model=TestModel(), csrf_exempt=True, **kwargs)


spa = _server(
    id="spa",
    name="Board",
    description="The board agent",
    namespace="spa",
    get_user=token_user,
    conversation_store=DefaultConversationStore(),
    run_store=DefaultRunStore(),
    skills=SkillRegistry(),
)
resumer = _server(
    id="resumer",
    namespace="resumer",
    get_user=token_user,
    conversation_store=DefaultConversationStore(),
    run_store=DefaultRunStore(),
    step_store=DefaultStepStore,
)
admin = _server(id="admin", namespace="admin_ns", conversation_store=DefaultConversationStore())
bare = _server(id="bare", namespace="bare")
denier = _server(id="denier", namespace="denier", authorize=lambda request: False)

forward = AgentsDirectory([spa, admin, denier], namespace="dir")
backward = AgentsDirectory([denier, admin, spa], namespace="dir_rev")
only_denier = AgentsDirectory([denier], namespace="dir_denier")
resuming = AgentsDirectory([resumer], namespace="dir_resuming")

urlpatterns = [
    path("agent/", spa.urls),
    path("admin-agent/", admin.urls),
    path("denied/", denier.urls),
    path("bare/", bare.urls),
    path("home/", forward.urls),
    path("home-reversed/", backward.urls),
    path("home-denied/", only_denier.urls),
    path("resumer/", resumer.urls),
    path("home-resuming/", resuming.urls),
]
