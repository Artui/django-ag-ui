from __future__ import annotations

from typing import Any

from django.test import override_settings
from django.urls import resolve, reverse
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import DEFAULT_NAMESPACE, AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.skills.skill_registry import SkillRegistry


def _server(**kwargs: Any) -> AGUIServer:
    return AGUIServer(ToolRegistry(), model=TestModel(), **kwargs)


def _names(server: AGUIServer) -> set[str]:
    patterns, _, _ = server.urls
    return {p.name for p in patterns}


def test_urls_returns_admin_style_triple() -> None:
    # ``(patterns, app_name, namespace)`` — the shape ``path()`` mounts directly,
    # like ``admin.site.urls`` (no ``include()``).
    patterns, app_name, namespace = _server().urls
    assert app_name == namespace == DEFAULT_NAMESPACE == "ag_ui"
    assert isinstance(patterns, list)


def test_namespace_is_overridable() -> None:
    _, app_name, namespace = _server(namespace="agent").urls
    assert app_name == namespace == "agent"


def test_bare_server_mounts_only_endpoint_and_tools() -> None:
    assert _names(_server()) == {"endpoint", "tools"}


def test_endpoint_mounts_at_the_prefix_root() -> None:
    patterns, _, _ = _server().urls
    endpoint = next(p for p in patterns if p.name == "endpoint")
    assert str(endpoint.pattern) == ""


@override_settings(ROOT_URLCONF="tests.agent.agui_server_urls")
def test_urls_mount_directly_via_path_and_reverse_namespaced() -> None:
    # The admin.site.urls idiom: path("agent/", server.urls) with no include().
    assert reverse("ag_ui:endpoint") == "/agent/"
    assert reverse("ag_ui:tools") == "/agent/tools/"
    assert reverse("ag_ui:skills") == "/agent/skills/"
    assert reverse("ag_ui:threads") == "/agent/threads/"
    assert reverse("ag_ui:thread", kwargs={"thread_id": "abc"}) == "/agent/threads/abc/"
    match = resolve("/agent/")
    assert match.namespace == "ag_ui"
    assert match.url_name == "endpoint"


def test_tools_view_reuses_the_registry() -> None:
    registry = ToolRegistry()
    patterns, _, _ = AGUIServer(registry, model=TestModel()).urls
    tools = next(p for p in patterns if p.name == "tools")
    assert "tools/" in str(tools.pattern)
    assert isinstance(tools.callback, ToolsView)


def test_endpoint_view_is_the_built_agent_view() -> None:
    patterns, _, _ = _server().urls
    endpoint = next(p for p in patterns if p.name == "endpoint")
    assert isinstance(endpoint.callback, DjangoAGUIView)


def test_step_store_factory_forwards_to_the_agent_view() -> None:
    from django_pydantic_agent.contrib.store.default_step_store import DefaultStepStore

    server = _server(step_store=DefaultStepStore)
    assert server._view._step_store is DefaultStepStore


def test_step_store_defaults_to_none() -> None:
    assert _server()._view._step_store is None


def test_resume_and_fork_endpoints_mount_with_a_step_store() -> None:
    from django_pydantic_agent.contrib.store.default_step_store import DefaultStepStore

    patterns, _, _ = _server(step_store=DefaultStepStore).urls
    names = {p.name for p in patterns}
    assert {"resume", "fork"} <= names
    resume = next(p for p in patterns if p.name == "resume")
    # The path converter is named ``resume_from`` so Django hands it to the view.
    assert "resume/<str:resume_from>/" in str(resume.pattern)


def test_resume_and_fork_omitted_without_a_step_store() -> None:
    assert {"resume", "fork"}.isdisjoint(_names(_server()))


def test_skills_endpoint_mounts_when_registry_passed() -> None:
    patterns, _, _ = _server(skills=SkillRegistry()).urls
    skills = next(p for p in patterns if p.name == "skills")
    assert "skills/" in str(skills.pattern)


def test_skills_endpoint_omitted_by_default() -> None:
    assert "skills" not in _names(_server())


def test_thread_endpoints_mount_for_a_non_null_store() -> None:
    patterns, _, _ = _server(conversation_store=_DummyStore()).urls
    collection = next(p for p in patterns if p.name == "threads")
    detail = next(p for p in patterns if p.name == "thread")
    assert "threads/" in str(collection.pattern)
    assert "threads/<str:thread_id>/" in str(detail.pattern)
    assert isinstance(collection.callback, ThreadsView)


def test_thread_endpoints_omitted_for_null_store() -> None:
    names = _names(_server(conversation_store=NullConversationStore()))
    assert "threads" not in names
    assert "thread" not in names


def test_attachment_endpoints_mount_for_a_non_null_store() -> None:
    patterns, _, _ = _server(attachment_store=_DummyAttachmentStore()).urls
    collection = next(p for p in patterns if p.name == "attachments")
    detail = next(p for p in patterns if p.name == "attachment")
    assert "attachments/" in str(collection.pattern)
    assert "attachments/<str:attachment_id>/" in str(detail.pattern)


def test_attachment_endpoints_omitted_by_default() -> None:
    names = _names(_server())
    assert "attachments" not in names
    assert "attachment" not in names


def test_transcribe_endpoint_mounts_for_a_non_null_backend() -> None:
    patterns, _, _ = _server(transcription_backend=_DummyTranscriptionBackend()).urls
    transcribe = next(p for p in patterns if p.name == "transcribe")
    assert "transcribe/" in str(transcribe.pattern)


def test_transcribe_endpoint_omitted_by_default() -> None:
    assert "transcribe" not in _names(_server())


def test_passing_a_store_mounts_the_thread_endpoints() -> None:
    from django_pydantic_agent.persistence.django_session_conversation_store import (
        DjangoSessionConversationStore,
    )

    server = AGUIServer(
        ToolRegistry(), model=TestModel(), conversation_store=DjangoSessionConversationStore()
    )
    assert "threads" in _names(server)


def test_no_store_means_no_thread_endpoints() -> None:
    """Stores are passed, never resolved from a dotted path — so a server with
    none gets the Null store and doesn't mount the sub-views."""
    assert "threads" not in _names(AGUIServer(ToolRegistry(), model=TestModel()))


def test_auth_policy_forwards_to_every_view() -> None:
    server = _server(
        skills=SkillRegistry(),
        conversation_store=_DummyStore(),
        require_authenticated=True,
    )
    patterns, _, _ = server.urls
    for pattern in patterns:
        assert pattern.callback._require_authenticated is True


class _DummyStore:
    """A minimal non-``Null`` conversation store — only its type matters here."""


class _DummyAttachmentStore:
    """A minimal non-``Null`` attachment store — only its type matters here."""


class _DummyTranscriptionBackend:
    """A minimal non-``Null`` transcription backend — only its type matters here."""
