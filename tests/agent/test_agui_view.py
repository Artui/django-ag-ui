from __future__ import annotations

import asyncio
import json
import warnings
from types import SimpleNamespace

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


@override_settings(DJANGO_AG_UI={"FORWARD_REASONING": False})
async def test_reasoning_opt_out_still_streams_the_answer() -> None:
    # With FORWARD_REASONING off the stream is wrapped in the reasoning filter;
    # a normal run (TestModel emits no reasoning) must still stream end-to-end.
    view = DjangoAGUIView(_registry(), model=TestModel())
    response = await view(_post(_run_input("double 5")))
    body = await _drain(response)
    assert "RUN_STARTED" in body
    assert "RUN_FINISHED" in body
    assert "REASONING" not in body


def test_view_is_marked_as_a_coroutine_function() -> None:
    # Django's handler must detect __call__ as async and await it when the
    # view is mounted; otherwise it returns an unawaited coroutine.
    from asgiref.sync import iscoroutinefunction

    assert iscoroutinefunction(DjangoAGUIView(_registry(), model=TestModel()))


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


@override_settings(
    DJANGO_AG_UI={"MODEL": "anthropic:claude-sonnet-4-5", "API_KEY": "sk-test"},
)
async def test_api_key_builds_a_model_with_an_explicit_provider() -> None:
    from pydantic_ai.models.anthropic import AnthropicModel

    view = DjangoAGUIView(_registry())
    resolved = view._resolve_model()
    assert isinstance(resolved, AnthropicModel)


async def test_anonymous_is_rejected_when_require_authenticated() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel(), require_authenticated=True)
    # RequestFactory builds no `.user`, so the request is unauthenticated.
    response = await view(_post(_run_input("hi")))
    assert response.status_code == 401


async def test_authenticated_user_passes_the_gate() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel(), require_authenticated=True)
    request = _post(_run_input("hi"))
    request.user = SimpleNamespace(is_authenticated=True)
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)


async def test_get_user_hook_establishes_the_user() -> None:
    user = SimpleNamespace(is_authenticated=True, username="api")
    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        require_authenticated=True,
        get_user=lambda _request: user,
    )
    request = _post(_run_input("hi"))
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    assert request.user is user


async def test_warns_when_served_over_wsgi() -> None:
    # RequestFactory builds a WSGIRequest; SSE won't stream there.
    view = DjangoAGUIView(_registry(), model=TestModel())
    with pytest.warns(RuntimeWarning, match="ASGI"):
        await view(RequestFactory().get("/agent/"))


async def test_does_not_warn_under_asgi() -> None:
    from django.test import AsyncRequestFactory

    view = DjangoAGUIView(_registry(), model=TestModel())
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any RuntimeWarning would raise
        # GET → 405, but the ASGIRequest path must not warn.
        await view(AsyncRequestFactory().get("/agent/"))


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
    agent = view._build_agent(RequestFactory().post("/agent/"))
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


async def test_drf_mcp_toolset_built_per_request_when_configured() -> None:
    from django_ag_ui.integrations.drf_mcp import DrfMcpToolset

    view = DjangoAGUIView(_registry(), model=TestModel())
    request = RequestFactory().post("/agent/")
    toolsets = view._drf_mcp_toolsets("tests.integrations.drf_server.server", request)
    assert len(toolsets) == 1
    assert isinstance(toolsets[0], DrfMcpToolset)


async def test_no_drf_mcp_toolset_without_the_setting() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    assert view._drf_mcp_toolsets(None, RequestFactory().post("/agent/")) == []


_DEFAULT_ATTACHMENT_STORE = (
    "django_ag_ui.contrib.store.default_attachment_store.DefaultAttachmentStore"
)


async def test_attachment_toolset_built_per_request_when_configured() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    toolsets = view._attachment_toolsets(
        _DEFAULT_ATTACHMENT_STORE, RequestFactory().post("/agent/")
    )
    assert len(toolsets) == 1
    assert toolsets[0].id == "django-ag-ui-attachments"


async def test_no_attachment_toolset_without_the_setting() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    assert view._attachment_toolsets(None, RequestFactory().post("/agent/")) == []


async def test_attachment_toolset_skipped_when_registry_owns_the_name() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def read_attachment(attachment_id: str) -> str:
        """A consumer's own read_attachment wins over the built-in."""
        return attachment_id

    view = DjangoAGUIView(reg, model=TestModel())
    assert (
        view._attachment_toolsets(_DEFAULT_ATTACHMENT_STORE, RequestFactory().post("/agent/")) == []
    )


@pytest.mark.django_db(transaction=True)
async def test_sync_orm_get_user_hook_works_under_async() -> None:
    # The headline use case: a *sync* hook doing a real ORM lookup. Before
    # the sync-or-async fix this raised SynchronousOnlyOperation (the hook
    # ran on the event loop).
    from django.contrib.auth.models import User

    def get_user(request):  # noqa: ANN001, ANN202 — the shape adapters write
        return User.objects.get_or_create(username="api")[0]

    view = DjangoAGUIView(
        _registry(), model=TestModel(), require_authenticated=True, get_user=get_user
    )
    request = _post(_run_input("hi"))
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    assert request.user.username == "api"


async def test_async_get_user_hook_is_awaited() -> None:
    # Previously an async hook was called without awaiting → a coroutine
    # landed on request.user and the gate silently failed.
    user = SimpleNamespace(is_authenticated=True, username="async-api")

    async def get_user(request):  # noqa: ANN001, ANN202
        return user

    view = DjangoAGUIView(
        _registry(), model=TestModel(), require_authenticated=True, get_user=get_user
    )
    request = _post(_run_input("hi"))
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    assert request.user is user


async def test_sync_hook_returning_a_coroutine_is_awaited() -> None:
    # Belt-and-suspenders: a sync callable wrapping an async fn (e.g. a
    # functools.partial) must never leak a coroutine onto request.user.
    user = SimpleNamespace(is_authenticated=True)

    async def _lookup() -> SimpleNamespace:
        return user

    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        require_authenticated=True,
        get_user=lambda _request: _lookup(),
    )
    request = _post(_run_input("hi"))
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    assert request.user is user


async def test_hook_returning_anonymous_is_rejected() -> None:
    from django.contrib.auth.models import AnonymousUser

    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        require_authenticated=True,
        get_user=lambda _request: AnonymousUser(),
    )
    response = await view(_post(_run_input("hi")))
    assert response.status_code == 401


class _SpyAuditLogger:
    def __init__(self) -> None:
        self.events = []  # type: ignore[var-annotated]

    def record(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


def _blocking_model(closed: asyncio.Event):  # noqa: ANN202
    """A model that streams two text deltas, then holds the stream open.

    The ``finally`` records whether the provider stream was torn down — the
    cancellation test's proof that a client disconnect doesn't leave an
    orphaned upstream generation.
    """
    from pydantic_ai.models.function import FunctionModel

    async def stream_fn(messages, info):  # noqa: ANN001, ANN202
        try:
            yield "partial "
            yield "answer"
            await asyncio.Event().wait()  # parked until cancellation unwinds the run
        finally:
            closed.set()

    return FunctionModel(stream_function=stream_fn)


async def _cancel_mid_stream(response: StreamingHttpResponse, marker: str) -> None:
    """Consume the SSE stream until ``marker`` appears, then cancel the consumer.

    Mirrors Django's ASGI handler on ``http.disconnect``: the task consuming
    the response is cancelled, so ``CancelledError`` lands at the innermost
    ``await`` of the streaming chain.
    """
    saw_marker = asyncio.Event()

    async def _consume() -> None:
        async for chunk in response.streaming_content:
            text = chunk if isinstance(chunk, str) else chunk.decode()
            if marker in text:
                saw_marker.set()

    task = asyncio.ensure_future(_consume())
    await asyncio.wait_for(saw_marker.wait(), timeout=5)
    # Let the consumer park at the next __anext__ (blocked on the model) so
    # the cancellation is delivered inside the streaming chain.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@override_settings(
    DJANGO_AG_UI={
        "CONVERSATION_STORE": (
            "django_ag_ui.persistence.django_session_conversation_store"
            ".DjangoSessionConversationStore"
        ),
    },
)
async def test_disconnect_persists_partial_audits_and_closes_the_model_stream() -> None:
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    from django_ag_ui.persistence.django_session_conversation_store import (
        DjangoSessionConversationStore,
    )

    model_stream_closed = asyncio.Event()
    spy = _SpyAuditLogger()
    view = DjangoAGUIView(_registry(), model=_blocking_model(model_stream_closed), audit_logger=spy)
    request = _post(_run_input("hi"))
    request.session = SessionStore()
    response = await view(request)

    await _cancel_mid_stream(response, "answer")

    # Provider teardown: the model's stream context was exited, not orphaned.
    assert model_stream_closed.is_set()

    # Partial persistence: the truncated exchange — client history plus the
    # partially streamed assistant text — landed in the configured store.
    loaded = await DjangoSessionConversationStore().load("t1", request=request)
    assert loaded is not None
    contents = [message.content for message in loaded.messages]
    assert "hi" in contents
    assert "partial answer" in contents

    # Audit: the cancellation is a distinguishable run-level record.
    (event,) = spy.events
    assert event.tool_name == "agent.run"
    assert event.success is False
    assert event.error.startswith("cancelled:")
    assert "t1" in event.arguments_repr
    assert "r1" in event.arguments_repr
    assert event.duration_ms > 0


async def test_disconnect_without_a_store_still_audits_and_reraises() -> None:
    # Default settings → NullConversationStore: no save attempted, no error,
    # and the cancellation still re-raises (asserted inside the helper).
    model_stream_closed = asyncio.Event()
    spy = _SpyAuditLogger()
    view = DjangoAGUIView(_registry(), model=_blocking_model(model_stream_closed), audit_logger=spy)
    response = await view(_post(_run_input("hi")))

    await _cancel_mid_stream(response, "answer")

    assert model_stream_closed.is_set()
    (event,) = spy.events
    assert event.tool_name == "agent.run"
    assert event.success is False


@pytest.mark.django_db(transaction=True)
async def test_lazy_request_user_is_materialized_off_the_loop() -> None:
    # ASYNC-1: with DB-backed sessions, request.user is a SimpleLazyObject
    # whose first touch runs ORM queries — forbidden on the event loop. The
    # view must resolve it in a worker thread, so this passes instead of
    # raising SynchronousOnlyOperation.
    from django.contrib.auth.models import User
    from django.utils.functional import SimpleLazyObject

    view = DjangoAGUIView(_registry(), model=TestModel(), require_authenticated=True)
    request = _post(_run_input("hi"))
    request.user = SimpleLazyObject(lambda: User.objects.get_or_create(username="lazy")[0])
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    assert request.user.username == "lazy"
