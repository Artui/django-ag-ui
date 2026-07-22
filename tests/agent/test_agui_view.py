from __future__ import annotations

import asyncio
import json
import warnings
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import StreamingHttpResponse
from django.test import RequestFactory, override_settings
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.contrib.store.default_step_store import DefaultStepStore
from django_ag_ui.registry.decorator import tool
from django_ag_ui.registry.tool_registry import ToolRegistry


def _run_input(content: str, run_id: str = "r1") -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": run_id,
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


@pytest.mark.django_db
async def test_service_specs_tool_runs_in_process() -> None:
    # The no-MCP-hop path: a drf-services spec passed as service_specs=
    # is wired as a SpecToolset and executed in-process during the run. A
    # get_user hook stands in for the auth middleware that sets request.user in
    # a real deployment (the toolset binds it as the acting user).
    from tests.integrations.drf_specs import SPECS

    view = DjangoAGUIView(
        ToolRegistry(),
        model=TestModel(call_tools=["ping"]),
        get_user=lambda _request: AnonymousUser(),
        service_specs=SPECS,
    )
    response = await view(_post(_run_input("ping the server")))
    body = await _drain(response)
    assert "RUN_FINISHED" in body
    assert "ping" in body


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


async def test_audit_logger_defaults_to_the_null_logger() -> None:
    """No dotted path to resolve: the logger is passed, or there is none."""
    from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger

    view = DjangoAGUIView(_registry(), model=TestModel())
    assert isinstance(view._resolve_audit_logger(), NullAuditLogger)


async def test_audit_logger_is_passed_not_named() -> None:
    from django_ag_ui.policy.audit.logging_audit_logger import LoggingAuditLogger

    view = DjangoAGUIView(_registry(), model=TestModel(), audit_logger=LoggingAuditLogger())
    assert isinstance(view._resolve_audit_logger(), LoggingAuditLogger)


async def test_explicit_audit_logger_wins() -> None:
    from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger

    sentinel = NullAuditLogger()
    view = DjangoAGUIView(_registry(), model=TestModel(), audit_logger=sentinel)
    assert view._resolve_audit_logger() is sentinel


async def test_agent_factory_escape_hatch_takes_over_construction() -> None:
    # No model passed and none in settings — the factory supplies the model,
    # proving it fully replaces the built-in construction (no MODEL required).
    from tests.agent.factories import build_test_agent

    view = DjangoAGUIView(_registry(), agent_factory=build_test_agent)
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
    agent = view._build_agent(RequestFactory().post("/agent/"), SimpleNamespace(run_id="r1"))
    assert isinstance(agent, Agent)


async def test_conversation_is_persisted_when_a_store_is_configured() -> None:
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    from django_ag_ui.persistence.django_session_conversation_store import (
        DjangoSessionConversationStore,
    )

    view = DjangoAGUIView(
        _registry(), model=TestModel(), conversation_store=DjangoSessionConversationStore()
    )
    request = _post(_run_input("double 5"))
    request.session = SessionStore()
    response = await view(request)
    await _drain(response)

    # The run's full message history was mirrored into the session store.
    loaded = await DjangoSessionConversationStore().load("t1", request=request)
    assert loaded is not None
    assert loaded.thread_id == "t1"
    assert len(loaded.messages) >= 1


@pytest.mark.django_db(transaction=True)
async def test_anonymous_run_skips_persistence_when_the_store_refuses() -> None:
    # An open agent endpoint + a model store that refuses anonymous writes (the
    # default, no ALLOW_ANONYMOUS): the run streams to completion and the save is
    # skipped rather than crashing the stream — no row is written.
    from django_ag_ui.contrib.store.default_conversation_store import (
        DefaultConversationStore,
    )
    from django_ag_ui.contrib.store.models import StoredConversation

    view = DjangoAGUIView(
        _registry(), model=TestModel(), conversation_store=DefaultConversationStore()
    )
    response = await view(_post(_run_input("double 5")))  # anonymous request
    body = await _drain(response)
    assert "RUN_FINISHED" in body
    assert await StoredConversation.objects.acount() == 0


async def test_drf_mcp_toolset_built_per_request_when_configured() -> None:
    from django_ag_ui.integrations.drf_mcp import DRFMCPToolset

    view = DjangoAGUIView(_registry(), model=TestModel())
    request = RequestFactory().post("/agent/")
    from tests.integrations.drf_server import server as drf_server

    toolsets = view._drf_mcp_toolsets(drf_server, request, set())
    assert len(toolsets) == 1
    assert isinstance(toolsets[0], DRFMCPToolset)


async def test_no_drf_mcp_toolset_without_the_setting() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    assert view._drf_mcp_toolsets(None, RequestFactory().post("/agent/"), set()) == []


_DEFAULT_ATTACHMENT_STORE = (
    "django_ag_ui.contrib.store.default_attachment_store.DefaultAttachmentStore"
)


async def test_attachment_toolset_built_per_request_when_configured() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    toolsets = view._attachment_toolsets(
        _DEFAULT_ATTACHMENT_STORE, RequestFactory().post("/agent/"), set()
    )
    assert len(toolsets) == 1
    assert toolsets[0].id == "django-ag-ui-attachments"


async def test_no_attachment_toolset_without_the_setting() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    assert view._attachment_toolsets(None, RequestFactory().post("/agent/"), set()) == []


async def test_attachment_toolset_skipped_when_a_prior_tool_owns_the_name() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    # ``read_attachment`` already claimed upstream (registry / drf-mcp / spec):
    # the attachment toolset yields to it rather than raising a duplicate.
    seen = {"read_attachment"}
    assert (
        view._attachment_toolsets(_DEFAULT_ATTACHMENT_STORE, RequestFactory().post("/agent/"), seen)
        == []
    )


async def test_seen_set_guards_three_way_name_collisions() -> None:
    # drf-mcp → spec → attachment precedence, threaded through one ``seen``
    # set so a name exposed by two sources can't reach pydantic-ai as a duplicate.
    from tests.integrations.drf_server import server as drf_server
    from tests.integrations.drf_specs_colliding import SPECS as colliding_specs

    view = DjangoAGUIView(_registry(), model=TestModel())
    request = RequestFactory().post("/agent/")
    seen: set[str] = set()

    # drf-mcp reserves its server's tool names.
    view._drf_mcp_toolsets(drf_server, request, seen)
    assert {"add", "invalid", "denied"} <= seen

    # spec: ``add`` collides with drf-mcp (dropped, drf-mcp wins); ``unique_spec``
    # survives; ``read_attachment`` is now reserved by the spec capability.
    (spec_capability,) = view._spec_capabilities(colliding_specs, request, seen)
    spec_names = set(spec_capability.get_toolset()._specs)
    assert "add" not in spec_names
    assert "unique_spec" in spec_names
    assert "read_attachment" in seen

    # attachment: yields because a spec already claimed ``read_attachment``.
    assert view._attachment_toolsets(_DEFAULT_ATTACHMENT_STORE, request, seen) == []


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


async def test_disconnect_persists_partial_audits_and_closes_the_model_stream() -> None:
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    from django_ag_ui.persistence.django_session_conversation_store import (
        DjangoSessionConversationStore,
    )

    model_stream_closed = asyncio.Event()
    spy = _SpyAuditLogger()
    view = DjangoAGUIView(
        _registry(),
        model=_blocking_model(model_stream_closed),
        audit_logger=spy,
        conversation_store=DjangoSessionConversationStore(),
    )
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
    # No conversation_store passed → NullConversationStore: no save attempted,
    # no error, and the cancellation still re-raises (asserted in the helper).
    model_stream_closed = asyncio.Event()
    spy = _SpyAuditLogger()
    view = DjangoAGUIView(
        _registry(),
        model=_blocking_model(model_stream_closed),
        audit_logger=spy,
    )
    response = await view(_post(_run_input("hi")))

    await _cancel_mid_stream(response, "answer")

    assert model_stream_closed.is_set()
    (event,) = spy.events
    assert event.tool_name == "agent.run"
    assert event.success is False


@pytest.mark.django_db(transaction=True)
async def test_lazy_request_user_is_materialized_off_the_loop() -> None:
    # With DB-backed sessions, request.user is a SimpleLazyObject
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


# --- Step persistence wiring --------------------------------------------------


def test_step_persistence_capability_built_when_a_store_is_configured() -> None:
    from pydantic_ai_harness.step_persistence import StepPersistence

    view = DjangoAGUIView(_registry(), model=TestModel(), step_store=DefaultStepStore)
    caps = view._step_persistence_capabilities(_post(b""), SimpleNamespace(run_id="run-9"))
    assert len(caps) == 1
    assert isinstance(caps[0], StepPersistence)
    assert caps[0].run_id == "run-9"
    assert isinstance(caps[0].store, DefaultStepStore)


def test_no_step_persistence_capability_without_a_store() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    caps = view._step_persistence_capabilities(_post(b""), SimpleNamespace(run_id="x"))
    assert caps == []


@pytest.mark.django_db(transaction=True)
async def test_step_store_records_the_run_end_to_end() -> None:
    from asgiref.sync import sync_to_async
    from django.contrib.auth import get_user_model

    from django_ag_ui.contrib.store.models import (
        StoredRun,
        StoredStepEvent,
        StoredToolEffect,
    )

    user = await sync_to_async(get_user_model().objects.create)(username="stepper")
    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        step_store=DefaultStepStore,
        get_user=lambda request: user,
    )
    body = await _drain(await view(_post(_run_input("double 5"))))
    assert "RUN_FINISHED" in body

    owner = str(user.pk)
    # The StepPersistence capability recorded this run, its lifecycle events, and
    # the tool effect for the "double" call it exercised — all owner-scoped.
    assert await StoredRun.objects.filter(owner_id=owner, run_id="r1").acount() == 1
    assert await StoredStepEvent.objects.filter(owner_id=owner, run_id="r1").aexists()
    assert await StoredToolEffect.objects.filter(owner_id=owner, run_id="r1").aexists()


# --- Resume / fork ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_resume_seeds_a_new_run_from_a_prior_snapshot() -> None:
    from asgiref.sync import sync_to_async
    from django.contrib.auth import get_user_model

    from django_ag_ui.contrib.store.models import StoredRun, StoredSnapshot

    user = await sync_to_async(get_user_model().objects.create)(username="resumer")
    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        step_store=DefaultStepStore,
        get_user=lambda _request: user,
    )
    owner = str(user.pk)

    # A first run "r1" records a continuable snapshot for this owner.
    await _drain(await view(_post(_run_input("double 5", run_id="r1"))))
    assert await StoredSnapshot.objects.filter(owner_id=owner, run_id="r1").aexists()

    # Resume from r1 into a fresh run r2 that sends only a new turn.
    resumed = await _drain(await view(_post(_run_input("double 9", run_id="r2")), resume_from="r1"))
    assert "RUN_FINISHED" in resumed

    # r2 is a distinct run linked back to r1 (parent preserved, r1 untouched)...
    r2 = await StoredRun.objects.aget(owner_id=owner, run_id="r2")
    assert r2.parent_run_id == "r1"
    r1 = await StoredRun.objects.aget(owner_id=owner, run_id="r1")
    assert r1.parent_run_id is None
    # ...and r2's snapshot carries the injected r1 history *and* the new turn.
    snap = await StoredSnapshot.objects.filter(owner_id=owner, run_id="r2").order_by("-id").afirst()
    dumped = json.dumps(snap.messages)
    assert "double 5" in dumped
    assert "double 9" in dumped


@pytest.mark.django_db(transaction=True)
async def test_resume_of_an_unknown_run_is_404() -> None:
    from asgiref.sync import sync_to_async
    from django.contrib.auth import get_user_model

    user = await sync_to_async(get_user_model().objects.create)(username="u")
    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        step_store=DefaultStepStore,
        get_user=lambda _request: user,
    )
    response = await view(_post(_run_input("hi", run_id="r2")), resume_from="ghost")
    assert response.status_code == 404
    payload = json.loads(response.content)
    assert payload["error"] == "no resumable run"
    assert payload["run_id"] == "ghost"


async def test_resume_without_a_step_store_is_404() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())  # no step_store
    response = await view(_post(_run_input("hi")), resume_from="r1")
    assert response.status_code == 404
    assert json.loads(response.content)["error"] == "no resumable run"


@pytest.mark.django_db(transaction=True)
async def test_resume_cannot_reach_another_owners_run() -> None:
    from asgiref.sync import sync_to_async
    from django.contrib.auth import get_user_model

    User = get_user_model()
    owner_a = await sync_to_async(User.objects.create)(username="a")
    owner_b = await sync_to_async(User.objects.create)(username="b")

    # Owner A records run r1.
    view_a = DjangoAGUIView(
        _registry(), model=TestModel(), step_store=DefaultStepStore, get_user=lambda _r: owner_a
    )
    await _drain(await view_a(_post(_run_input("double 5", run_id="r1"))))

    # Owner B trying to resume A's run id sees a clean 404, not A's history.
    view_b = DjangoAGUIView(
        _registry(), model=TestModel(), step_store=DefaultStepStore, get_user=lambda _r: owner_b
    )
    response = await view_b(_post(_run_input("double 9", run_id="r2")), resume_from="r1")
    assert response.status_code == 404
