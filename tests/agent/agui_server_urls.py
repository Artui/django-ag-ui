"""A root URLconf mounting an :class:`AGUIServer` the ``admin.site.urls`` way.

Used by ``test_agui_server`` via ``@override_settings(ROOT_URLCONF=...)`` to
exercise the real ``path("agent/", server.urls)`` mount + namespaced
``reverse()`` — the contract the pattern-inspection tests can't see.
"""

from __future__ import annotations

from django.urls import path
from django_pydantic_agent.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_pydantic_agent.registry.tool_registry import ToolRegistry
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
