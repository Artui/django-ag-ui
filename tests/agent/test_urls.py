from __future__ import annotations

from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.urls import get_urls
from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.null_conversation_store import NullConversationStore
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.skills.skill_registry import SkillRegistry


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


def test_get_urls_mounts_skills_endpoint_when_given() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    patterns = get_urls(view, skills=SkillRegistry())
    assert len(patterns) == 2
    assert patterns[1].name == "django_ag_ui_skills"
    assert "agent/skills/" in str(patterns[1].pattern)


def test_get_urls_mounts_tools_endpoint_when_given() -> None:
    registry = ToolRegistry()
    view = DjangoAGUIView(registry, model=TestModel())
    patterns = get_urls(view, tools=registry)
    tools_pattern = next(p for p in patterns if p.name == "django_ag_ui_tools")
    assert "agent/tools/" in str(tools_pattern.pattern)


def test_get_urls_mounts_thread_endpoints_when_given() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    patterns = get_urls(view, threads=NullConversationStore())
    collection = next(p for p in patterns if p.name == "django_ag_ui_threads")
    detail = next(p for p in patterns if p.name == "django_ag_ui_thread")
    assert "agent/threads/" in str(collection.pattern)
    assert "threads/<str:thread_id>/" in str(detail.pattern)


def test_get_urls_omits_thread_endpoints_by_default() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    names = {p.name for p in get_urls(view)}
    assert "django_ag_ui_threads" not in names
    assert "django_ag_ui_thread" not in names


def test_get_urls_mounts_attachment_endpoints_when_given() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    patterns = get_urls(view, attachments=NullAttachmentStore())
    collection = next(p for p in patterns if p.name == "django_ag_ui_attachments")
    detail = next(p for p in patterns if p.name == "django_ag_ui_attachment")
    assert "agent/attachments/" in str(collection.pattern)
    assert "attachments/<str:attachment_id>/" in str(detail.pattern)


def test_get_urls_omits_attachment_endpoints_by_default() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    names = {p.name for p in get_urls(view)}
    assert "django_ag_ui_attachments" not in names
    assert "django_ag_ui_attachment" not in names


def test_get_urls_mounts_transcribe_endpoint_when_given() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    patterns = get_urls(view, transcribe=NullTranscriptionBackend())
    transcribe = next(p for p in patterns if p.name == "django_ag_ui_transcribe")
    assert "agent/transcribe/" in str(transcribe.pattern)


def test_get_urls_omits_transcribe_endpoint_by_default() -> None:
    view = DjangoAGUIView(ToolRegistry(), model=TestModel())
    names = {p.name for p in get_urls(view)}
    assert "django_ag_ui_transcribe" not in names
