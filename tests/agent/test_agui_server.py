from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import resolve, reverse
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import (
    DEFAULT_NAMESPACE,
    AGUIServer,
    _with_registry_prompts,
)
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.skills.skill_registry import SkillRegistry


def _server(**kwargs: Any) -> AGUIServer:
    return AGUIServer(ToolRegistry(), model=TestModel(), **kwargs)


def _names(server: AGUIServer) -> set[str]:
    patterns, _, _ = server.urls
    return {p.name for p in patterns}


def test_urls_returns_admin_style_triple() -> None:
    # The (patterns, app_name, namespace) shape path() mounts directly, like admin.site.urls.
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


def test_a_tools_own_confirm_becomes_its_approval_prompt() -> None:
    """One question, whichever end gates the call."""
    from django_spec_agents.registry.decorator import tool

    registry = ToolRegistry()

    @tool(registry, destructive=True, confirm="Delete the Basalt room?")
    def delete_room(name: str) -> str:
        """Delete a room."""
        return name

    resolved = _with_registry_prompts(build_ag_ui_config(), registry)

    assert resolved.approval_prompts == {"delete_room": "Delete the Basalt room?"}


def test_an_explicit_prompt_overrides_the_tools_own() -> None:
    from django_spec_agents.registry.decorator import tool

    registry = ToolRegistry()

    @tool(registry, destructive=True, confirm="Delete the Basalt room?")
    def delete_room(name: str) -> str:
        """Delete a room."""
        return name

    resolved = _with_registry_prompts(
        build_ag_ui_config(approval_prompts={"delete_room": "This cannot be undone. Sure?"}),
        registry,
    )

    assert resolved.approval_prompts == {"delete_room": "This cannot be undone. Sure?"}


def test_a_registry_with_no_confirms_leaves_the_map_alone() -> None:
    config = build_ag_ui_config()

    assert _with_registry_prompts(config, ToolRegistry()) is config


def test_endpoint_view_is_the_built_agent_view() -> None:
    patterns, _, _ = _server().urls
    endpoint = next(p for p in patterns if p.name == "endpoint")
    assert isinstance(endpoint.callback, DjangoAGUIView)


def test_step_store_factory_forwards_to_the_agent_view() -> None:
    from django_spec_agents.contrib.store.default_step_store import DefaultStepStore

    server = _server(step_store=DefaultStepStore)
    assert server._view._step_store is DefaultStepStore


def test_step_store_defaults_to_none() -> None:
    assert _server()._view._step_store is None


def test_resume_and_fork_endpoints_mount_with_a_step_store() -> None:
    from django_spec_agents.contrib.store.default_step_store import DefaultStepStore

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
    from django_spec_agents.persistence.django_session_conversation_store import (
        DjangoSessionConversationStore,
    )

    server = AGUIServer(
        ToolRegistry(), model=TestModel(), conversation_store=DjangoSessionConversationStore()
    )
    assert "threads" in _names(server)


def test_thread_activity_source_reaches_the_thread_view() -> None:
    """Both thread patterns are one view instance, so one check covers the route."""

    class _Source:
        async def activities_for(self, thread_id: str, **kwargs: Any) -> list[Any]:
            return []  # pragma: no cover - wiring is what this asserts

    source = _Source()
    patterns, _, _ = _server(conversation_store=_DummyStore(), thread_activity_source=source).urls
    thread = next(p for p in patterns if p.name == "thread")
    assert thread.callback._thread_activity_source is source


def test_no_activity_source_is_the_default() -> None:
    patterns, _, _ = _server(conversation_store=_DummyStore()).urls
    thread = next(p for p in patterns if p.name == "thread")
    assert thread.callback._thread_activity_source is None


def test_no_store_means_no_thread_endpoints() -> None:
    """Stores are passed, never resolved from a dotted path; with none, the Null store is used."""
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


def test_every_view_requires_authentication_by_default() -> None:
    """A bare mount serves nobody who is not logged in."""
    server = _server(
        skills=SkillRegistry(),
        conversation_store=_DummyStore(),
        attachment_store=_DummyAttachmentStore(),
        transcription_backend=_DummyTranscriptionBackend(),
    )
    patterns, _, _ = server.urls
    assert patterns
    for pattern in patterns:
        assert pattern.callback._require_authenticated is True, pattern.name


@pytest.mark.parametrize("stated", [True, False], ids=["exempt", "enforced"])
def test_csrf_reaches_every_view_in_the_mount(stated: bool) -> None:
    """One CSRF answer covers the whole mount, not the run endpoint alone."""
    from django_spec_agents.contrib.store.default_step_store import DefaultStepStore

    server = _server(
        csrf_exempt=stated,
        skills=SkillRegistry(),
        step_store=DefaultStepStore,
        conversation_store=_DummyStore(),
        attachment_store=_DummyAttachmentStore(),
        transcription_backend=_DummyTranscriptionBackend(),
    )
    patterns, _, _ = server.urls
    assert patterns
    for pattern in patterns:
        assert pattern.callback.csrf_exempt is stated, pattern.name


def test_unstated_csrf_is_exempt_across_the_whole_mount() -> None:
    # The default is unstated, which resolves to exempt everywhere the same way.
    with pytest.warns(RuntimeWarning, match="CSRF-exempt"):
        server = _server(
            conversation_store=_DummyStore(),
            attachment_store=_DummyAttachmentStore(),
        )
    patterns, _, _ = server.urls
    for pattern in patterns:
        assert pattern.callback.csrf_exempt is True, pattern.name


@override_settings(
    ROOT_URLCONF="tests.agent.agui_server_csrf_urls",
    MIDDLEWARE=["django.middleware.csrf.CsrfViewMiddleware"],
)
@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("post", "/assistant/attachments/"),
        ("delete", "/assistant/attachments/abc/"),
        ("patch", "/assistant/threads/abc/"),
        ("delete", "/assistant/threads/abc/"),
        ("post", "/assistant/transcribe/"),
    ],
)
def test_exempt_write_routes_reach_their_view_under_csrf_middleware(method: str, url: str) -> None:
    """The flag is only worth asserting if Django acts on it."""
    response = getattr(Client(enforce_csrf_checks=True), method)(url)
    assert response.status_code != 403


def test_the_csrf_guard_reaches_the_server_path() -> None:
    """The check lives on the view, so building a server inherits it."""
    with pytest.warns(RuntimeWarning, match="CSRF-exempt"):
        _server()


def test_stating_csrf_on_the_server_silences_the_guard() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _server(csrf_exempt=False)
        _server(csrf_exempt=True)


def test_authentication_can_be_waived_across_the_whole_mount() -> None:
    server = _server(
        skills=SkillRegistry(),
        conversation_store=_DummyStore(),
        require_authenticated=False,
    )
    patterns, _, _ = server.urls
    for pattern in patterns:
        assert pattern.callback._require_authenticated is False, pattern.name


class _DummyStore:
    """A minimal non-``Null`` conversation store — only its type matters here."""


class _DummyAttachmentStore:
    """A minimal non-``Null`` attachment store — only its type matters here."""


class _DummyTranscriptionBackend:
    """A minimal non-``Null`` transcription backend — only its type matters here."""


def test_runs_endpoint_mounts_with_a_step_store() -> None:
    from django_spec_agents.contrib.store.default_step_store import DefaultStepStore

    assert "runs" in _names(_server(step_store=DefaultStepStore))


def test_runs_endpoint_absent_without_a_step_store() -> None:
    # No store means no ledger to index, so the endpoint is not mounted (as resume/fork).
    assert "runs" not in _names(_server())


def test_runs_endpoint_carries_the_shared_auth_seam() -> None:
    from django_spec_agents.contrib.store.default_step_store import DefaultStepStore

    server = _server(step_store=DefaultStepStore, require_authenticated=True)
    patterns, _, _ = server.urls
    runs = next(p for p in patterns if p.name == "runs")

    assert runs.callback._require_authenticated is True


def test_throttle_forwards_to_the_agent_endpoint_only() -> None:
    """The run endpoint costs a model call per request; the cheap reads share the auth seam."""

    class _Throttle:
        def consume(self, request: Any) -> int | None:
            return None

    throttle = _Throttle()
    server = _server(skills=SkillRegistry(), conversation_store=_DummyStore(), throttle=throttle)
    patterns, _, _ = server.urls

    endpoint = next(p for p in patterns if p.name == "endpoint")
    assert endpoint.callback._throttle is throttle
    for pattern in patterns:
        if pattern.name != "endpoint":
            assert getattr(pattern.callback, "_throttle", None) is None


def test_no_throttle_by_default() -> None:
    patterns, _, _ = _server().urls
    endpoint = next(p for p in patterns if p.name == "endpoint")
    assert endpoint.callback._throttle is None


class _NoThrottle:
    def consume(self, request: Any) -> int | None:
        return None


def test_transcribe_throttle_reaches_the_transcribe_view() -> None:
    """The other route that spends provider money per request."""
    throttle = _NoThrottle()
    server = _server(
        transcription_backend=_DummyTranscriptionBackend(), transcribe_throttle=throttle
    )
    patterns, _, _ = server.urls

    transcribe = next(p for p in patterns if p.name == "transcribe")
    assert transcribe.callback._throttle is throttle


def test_the_two_throttles_stay_separate() -> None:
    """One limiter instance is one counter, so sharing would let clips eat runs."""
    runs, clips = _NoThrottle(), _NoThrottle()
    server = _server(
        transcription_backend=_DummyTranscriptionBackend(),
        throttle=runs,
        transcribe_throttle=clips,
    )
    patterns, _, _ = server.urls

    assert next(p for p in patterns if p.name == "endpoint").callback._throttle is runs
    assert next(p for p in patterns if p.name == "transcribe").callback._throttle is clips


def test_transcribe_is_unthrottled_by_default() -> None:
    patterns, _, _ = _server(transcription_backend=_DummyTranscriptionBackend()).urls
    transcribe = next(p for p in patterns if p.name == "transcribe")
    assert transcribe.callback._throttle is None


def test_the_agent_throttle_does_not_reach_transcribe() -> None:
    """Passing ``throttle=`` alone must not silently start limiting voice input."""
    patterns, _, _ = _server(
        transcription_backend=_DummyTranscriptionBackend(), throttle=_NoThrottle()
    ).urls
    assert next(p for p in patterns if p.name == "transcribe").callback._throttle is None


def test_the_runs_view_gets_the_endpoints_own_config() -> None:
    """Its list ceiling is per-endpoint, like every other limit on this mount."""
    from django_spec_agents.contrib.store.default_step_store import DefaultStepStore

    config = build_ag_ui_config(run_list_limit=3)
    server = AGUIServer(
        ToolRegistry(), model=TestModel(), step_store=DefaultStepStore, config=config
    )
    patterns, _, _ = server.urls

    assert next(p for p in patterns if p.name == "runs").callback._config.run_list_limit == 3
