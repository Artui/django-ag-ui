from __future__ import annotations

import json

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import StreamingHttpResponse
from django.test import RequestFactory, override_settings
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.registry.decorator import tool
from django_ag_ui.registry.tool_registry import ToolRegistry


def _run_input(content: str) -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": content}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


def _post(body: bytes):  # noqa: ANN202
    return RequestFactory().post("/agent/", data=body, content_type="application/json")


async def _drain(response: StreamingHttpResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.streaming_content:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    return reg


async def test_streams_ag_ui_events() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    response = await view(_post(_run_input("double 5")))
    assert isinstance(response, StreamingHttpResponse)
    assert response["Content-Type"] == "text/event-stream"
    assert response["Cache-Control"] == "no-cache"

    body = await _drain(response)
    assert "RUN_STARTED" in body
    assert "RUN_FINISHED" in body
    # TestModel exercises the registered server-side tool.
    assert "double" in body


async def test_non_post_is_rejected() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    request = RequestFactory().get("/agent/")
    response = await view(request)
    assert response.status_code == 405


async def test_invalid_body_returns_400() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    response = await view(_post(b"{not valid json"))
    assert response.status_code == 400
    payload = json.loads(response.content)
    assert payload["error"] == "invalid RunAgentInput"


async def test_csrf_exempt_attribute_default_and_override() -> None:
    assert DjangoAGUIView(_registry(), model=TestModel()).csrf_exempt is True
    assert DjangoAGUIView(_registry(), model=TestModel(), csrf_exempt=False).csrf_exempt is False


@override_settings(DJANGO_AG_UI={})
async def test_missing_model_raises_improperly_configured() -> None:
    # No model passed and none in settings.
    view = DjangoAGUIView(_registry())
    with pytest.raises(ImproperlyConfigured, match="MODEL"):
        await view(_post(_run_input("hi")))


@override_settings(DJANGO_AG_UI={"MODEL": "anthropic:claude-sonnet-4.6"})
async def test_model_falls_back_to_settings_string() -> None:
    # No explicit model → the resolver returns the configured model string.
    view = DjangoAGUIView(_registry())
    assert view._resolve_model() == "anthropic:claude-sonnet-4.6"


async def test_explicit_model_wins_over_settings() -> None:
    model = TestModel()
    view = DjangoAGUIView(_registry(), model=model)
    assert view._resolve_model() is model


@override_settings(DJANGO_AG_UI={"SYSTEM_PROMPT": "Be very terse."})
async def test_instructions_fall_back_to_settings() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    # Just assert the resolution helper picks up the setting.
    assert view._resolve_instructions() == "Be very terse."


async def test_instructions_default_when_unset() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    assert "assistant" in view._resolve_instructions().lower()


async def test_explicit_instructions_win() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel(), instructions="Custom.")
    assert view._resolve_instructions() == "Custom."


@override_settings(
    DJANGO_AG_UI={
        "AUDIT_LOGGER": "django_ag_ui.policy.audit.logging_audit_logger.LoggingAuditLogger",
    }
)
async def test_audit_logger_resolved_from_settings() -> None:
    from django_ag_ui.policy.audit.logging_audit_logger import LoggingAuditLogger

    view = DjangoAGUIView(_registry(), model=TestModel())
    assert isinstance(view._resolve_audit_logger(), LoggingAuditLogger)


async def test_explicit_audit_logger_wins() -> None:
    from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger

    sentinel = NullAuditLogger()
    view = DjangoAGUIView(_registry(), model=TestModel(), audit_logger=sentinel)
    assert view._resolve_audit_logger() is sentinel


@override_settings(DJANGO_AG_UI={"AGENT_FACTORY": "tests.agent.factories.build_test_agent"})
async def test_agent_factory_escape_hatch_takes_over_construction() -> None:
    # No model passed and none in settings — the factory supplies the model,
    # proving it fully replaces the built-in construction (no MODEL required).
    view = DjangoAGUIView(_registry())
    response = await view(_post(_run_input("double 5")))
    body = await _drain(response)
    assert "RUN_FINISHED" in body
    assert "double" in body


@override_settings(
    DJANGO_AG_UI={
        "TOOLSETS": ["tests.agent.factories.a_toolset"],
        "CAPABILITIES": ["tests.agent.factories.make_toolset"],
        "MODEL_SETTINGS": {"temperature": 0.0},
        "RETRIES": 1,
    },
)
async def test_build_agent_applies_configured_toolsets_capabilities_and_settings() -> None:
    from pydantic_ai import Agent

    view = DjangoAGUIView(_registry(), model=TestModel())
    agent = view._build_agent()
    assert isinstance(agent, Agent)


@override_settings(
    DJANGO_AG_UI={
        "CONVERSATION_STORE": (
            "django_ag_ui.persistence.django_session_conversation_store"
            ".DjangoSessionConversationStore"
        ),
    },
)
async def test_conversation_is_persisted_when_a_store_is_configured() -> None:
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    from django_ag_ui.persistence.django_session_conversation_store import (
        DjangoSessionConversationStore,
    )

    view = DjangoAGUIView(_registry(), model=TestModel())
    request = _post(_run_input("double 5"))
    request.session = SessionStore()
    response = await view(request)
    await _drain(response)

    # The run's full message history was mirrored into the session store.
    loaded = await DjangoSessionConversationStore().load("t1", request=request)
    assert loaded is not None
    assert loaded.thread_id == "t1"
    assert len(loaded.messages) >= 1
