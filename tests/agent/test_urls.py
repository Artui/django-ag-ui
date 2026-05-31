from __future__ import annotations

from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.urls import get_urls
from django_ag_ui.registry.tool_registry import ToolRegistry


def test_get_urls_returns_single_named_pattern() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    patterns = get_urls(view)
    assert len(patterns) == 1
    assert patterns[0].name == "django_ag_ui"
    assert "agent/" in str(patterns[0].pattern)


def test_get_urls_honours_prefix() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    patterns = get_urls(view, prefix="ai/chat/")
    assert "ai/chat/" in str(patterns[0].pattern)
