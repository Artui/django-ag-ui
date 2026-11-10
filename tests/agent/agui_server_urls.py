from __future__ import annotations

from django.urls import path
from django_spec_agents.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.skills.skill_registry import SkillRegistry

_server = AGUIServer(
    ToolRegistry(),
    model=TestModel(),
    skills=SkillRegistry(),
    conversation_store=DjangoSessionConversationStore(),
)

urlpatterns = [path("agent/", _server.urls)]
