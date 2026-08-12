from __future__ import annotations

import asyncio
import json
import warnings
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import StreamingHttpResponse
from django.test import RequestFactory, override_settings
from django_pydantic_agent.contrib.store.default_step_store import DefaultStepStore
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.authed_request_factory import AuthedRequestFactory


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


def _post(body: bytes, *, anonymous: bool = False):  # noqa: ANN202
    # Authenticated by default: the endpoint refuses anonymous callers unless
    # told otherwise, so a fixture that wants to reach the agent has to be a
    # logged-in caller. ``anonymous=True`` is for the tests about that refusal.
    factory = RequestFactory() if anonymous else AuthedRequestFactory()
    return factory.post("/agent/", data=body, content_type="application/json")


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
    # is wired as a SpecToolset and executed in-process during the run. The
    # request arrives with a user the way the auth middleware sets one in a real
    # deployment (the toolset binds it as the acting user).
    from tests.integrations.drf_specs import SPECS

    view = DjangoAGUIView(
        ToolRegistry(),
        model=TestModel(call_tools=["ping"]),
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
    # Unstated resolves to exempt, so Django's middleware still reads a bool.
    assert DjangoAGUIView(_registry(), model=TestModel()).csrf_exempt is True
    assert DjangoAGUIView(_registry(), model=TestModel(), csrf_exempt=True).csrf_exempt is True
    assert DjangoAGUIView(_registry(), model=TestModel(), csrf_exempt=False).csrf_exempt is False


async def test_unstated_csrf_with_no_get_user_warns() -> None:
    # The combination the require_authenticated default cannot see: callers
    # authenticated by session cookie, on an endpoint with CSRF turned off.
    with pytest.warns(RuntimeWarning, match="CSRF-exempt"):
        DjangoAGUIView(_registry(), model=TestModel())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"csrf_exempt": False},
        {"csrf_exempt": True},
        {"get_user": lambda _request: SimpleNamespace(is_authenticated=True)},
    ],
    ids=["csrf-enforced", "csrf-exempt-deliberately", "get-user-hook"],
)
async def test_stating_how_requests_authenticate_silences_the_warning(kwargs: dict) -> None:
    # An explicit csrf_exempt=True is a *decision* (header-authenticated
    # clients, where CSRF does not apply), so it silences the warning just as
    # csrf_exempt=False does. Warning on the value rather than on the silence
    # would fire on a correct configuration with no way to say so.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        DjangoAGUIView(_registry(), model=TestModel(), **kwargs)


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


async def test_anonymous_is_rejected_by_default() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    # The plain factory builds no `.user`, so the request is unauthenticated —
    # and nothing had to be passed to have it refused.
    response = await view(_post(_run_input("hi"), anonymous=True))
    assert response.status_code == 401


async def test_anonymous_is_served_when_authentication_is_waived() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel(), require_authenticated=False)
    response = await view(_post(_run_input("hi"), anonymous=True))
    assert isinstance(response, StreamingHttpResponse)


async def test_authenticated_user_passes_the_gate() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    request = _post(_run_input("hi"))
    request.user = SimpleNamespace(is_authenticated=True)
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)


async def test_get_user_hook_establishes_the_user() -> None:
    user = SimpleNamespace(is_authenticated=True, username="api")
    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
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
    from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger

    view = DjangoAGUIView(_registry(), model=TestModel())
    assert isinstance(view._resolve_audit_logger(), NullAuditLogger)


async def test_audit_logger_is_passed_not_named() -> None:
    from django_pydantic_agent.policy.audit.logging_audit_logger import LoggingAuditLogger

    view = DjangoAGUIView(_registry(), model=TestModel(), audit_logger=LoggingAuditLogger())
    assert isinstance(view._resolve_audit_logger(), LoggingAuditLogger)


async def test_explicit_audit_logger_wins() -> None:
    from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger

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
    assert isinstance(view._agent(), Agent)


class TestTheAgentIsBuiltOnce:
    """Rebuilding per request re-derived every tool's JSON Schema per call."""

    async def test_two_runs_share_one_agent(self) -> None:
        view = DjangoAGUIView(_registry(), model=TestModel())

        await _drain(await view(_post(_run_input("double 5"))))
        first = view._agent()
        await _drain(await view(_post(_run_input("double 6"))))

        assert view._agent() is first

    async def test_two_endpoints_hold_two_agents(self) -> None:
        """Instance state, never module state — the package's standing rule."""
        one = DjangoAGUIView(_registry(), model=TestModel())
        other = DjangoAGUIView(_registry(), model=TestModel())

        assert one._agent() is not other._agent()

    async def test_the_agent_carries_no_instructions_of_its_own(self) -> None:
        """What makes per-request instructions a replacement rather than an
        append: pydantic-ai adds run instructions to the agent's, and adding to
        nothing is exactly setting."""
        view = DjangoAGUIView(_registry(), model=TestModel(), instructions="Be terse.")

        assert not view._agent()._instructions
        assert view._run_instructions(_post(_run_input("hi"))) == "Be terse."

    @override_settings(DJANGO_AG_UI={})
    async def test_a_missing_model_still_fails_on_the_request(self) -> None:
        """Lazy, not eager: the error keeps the timing it always had."""
        view = DjangoAGUIView(_registry())
        with pytest.raises(ImproperlyConfigured, match="MODEL"):
            await view(_post(_run_input("hi")))


class TestPerRequestOverrides:
    async def test_model_for_request_supplies_this_runs_model(self) -> None:
        seen: list[Any] = []
        view = DjangoAGUIView(
            _registry(),
            model=TestModel(),
            model_for_request=lambda request: seen.append(request) or TestModel(),
        )

        request = _post(_run_input("double 5"))
        await _drain(await view(request))

        assert seen == [request]

    async def test_instructions_for_request_replaces_the_default(self) -> None:
        view = DjangoAGUIView(
            _registry(),
            model=TestModel(),
            instructions="Endpoint default.",
            instructions_for_request=lambda _request: "Tenant-specific.",
        )

        assert view._run_instructions(_post(_run_input("hi"))) == "Tenant-specific."

    async def test_the_instructions_reach_the_model(self) -> None:
        """The end of the chain the agent's empty ``instructions`` opens up.

        Asserted against the messages the run actually sent, not against the
        value being forwarded — forwarding it to a parameter pydantic-ai treats
        as *additional* is the whole risk, and only the messages show whether
        "additional to nothing" really came out as the instructions.
        """
        view = DjangoAGUIView(
            _registry(),
            model=TestModel(),
            instructions="Endpoint default.",
            instructions_for_request=lambda _request: "Tenant-specific.",
        )
        request = _post(_run_input("double 5"))
        agent = view._agent()

        result = await agent.run(
            "double 5",
            instructions=view._run_instructions(request),
            deps=view._build_deps(request),
        )

        instructions = [
            m.instructions for m in result.all_messages() if getattr(m, "instructions", None)
        ]
        # One per model request (the initial turn and the tool-return turn):
        # what matters is that the endpoint default never appears.
        assert set(instructions) == {"Tenant-specific."}

    @override_settings(
        DJANGO_AG_UI={"MODEL": "anthropic:claude-sonnet-4-5", "API_KEY": "sk-test"},
    )
    async def test_a_hooks_model_string_gets_the_configured_credentials(self) -> None:
        """A hook returning "provider:name" is treated like the setting, rather
        than quietly falling back to environment inference."""
        from pydantic_ai.models.anthropic import AnthropicModel

        view = DjangoAGUIView(
            _registry(), model_for_request=lambda _request: "anthropic:claude-sonnet-4-5"
        )

        assert isinstance(view._run_model(_post(_run_input("hi"))), AnthropicModel)

    async def test_no_hook_leaves_the_run_on_the_endpoints_model(self) -> None:
        view = DjangoAGUIView(_registry(), model=TestModel())

        assert view._run_model(_post(_run_input("hi"))) is None


async def test_conversation_is_persisted_when_a_store_is_configured() -> None:
    from django.contrib.sessions.backends.signed_cookies import SessionStore
    from django_pydantic_agent.persistence.django_session_conversation_store import (
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
    from django_pydantic_agent.contrib.store.default_conversation_store import (
        DefaultConversationStore,
    )
    from django_pydantic_agent.contrib.store.models import StoredConversation

    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        conversation_store=DefaultConversationStore(),
        require_authenticated=False,
    )
    response = await view(_post(_run_input("double 5"), anonymous=True))
    body = await _drain(response)
    assert "RUN_FINISHED" in body
    assert await StoredConversation.objects.acount() == 0


async def test_drf_mcp_toolset_built_per_request_when_configured() -> None:
    from django_pydantic_agent.integrations.drf_mcp import DRFMCPToolset

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
    "django_pydantic_agent.contrib.store.default_attachment_store.DefaultAttachmentStore"
)


async def test_attachment_toolset_built_per_request_when_configured() -> None:
    view = DjangoAGUIView(_registry(), model=TestModel())
    toolsets = view._attachment_toolsets(
        _DEFAULT_ATTACHMENT_STORE, RequestFactory().post("/agent/"), set()
    )
    assert len(toolsets) == 1
    assert toolsets[0].id == "django-pydantic-agent-attachments"


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
    # drf-mcp -> spec -> attachment precedence, so a name exposed by two sources
    # can't reach pydantic-ai as a duplicate. Driven through the view's own
    # composition rather than the builders by hand: the name set is now computed
    # once at build time while the toolsets are still built per run, and the
    # thing worth guarding is that splitting them left the precedence intact.
    from tests.integrations.drf_server import server as drf_server
    from tests.integrations.drf_specs_colliding import SPECS as colliding_specs

    view = DjangoAGUIView(
        _registry(),
        model=TestModel(),
        drf_mcp_server=drf_server,
        service_specs=colliding_specs,
        attachment_store=_DEFAULT_ATTACHMENT_STORE,
    )
    request = RequestFactory().post("/agent/")

    # The registry's names and drf-mcp's are claimed before anything is built.
    claimed = view._claimed_names()
    assert {"double", "add", "invalid", "denied"} <= claimed

    # spec: ``add`` collides with drf-mcp (dropped, drf-mcp wins); ``unique_spec``
    # survives; ``read_attachment`` is claimed by the spec capability.
    (spec_capability,) = view._spec_capabilities(colliding_specs, claimed)
    spec_names = set(spec_capability.get_toolset()._specs)
    assert "add" not in spec_names
    assert "unique_spec" in spec_names

    # So the run gets the drf-mcp toolset and no attachment toolset — the spec
    # already owns ``read_attachment``.
    assert [type(t).__name__ for t in view._run_toolsets(request)] == ["DRFMCPToolset"]


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


async def test_each_request_audits_under_its_own_ip() -> None:
    """The failure a reused agent would otherwise introduce, and silently.

    The client IP used to be closed over when the agent was constructed. With
    one agent serving every run, that would stamp every audit record with the
    IP of whoever arrived first — well-formed records, wrong provenance, and
    nothing to notice it by. It rides the run's deps instead.
    """
    spy = _SpyAuditLogger()
    view = DjangoAGUIView(_registry(), model=TestModel(), audit_logger=spy)

    for ip in ("203.0.113.9", "198.51.100.4"):
        request = _post(_run_input("double 5"))
        request.META["REMOTE_ADDR"] = ip
        await _drain(await view(request))

    assert [e.ip_address for e in spy.events] == ["203.0.113.9", "198.51.100.4"]


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
    from django_pydantic_agent.persistence.django_session_conversation_store import (
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
    contents = [message["content"] for message in loaded.messages]
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
    from django_pydantic_agent.contrib.store.models import (
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
    from django_pydantic_agent.contrib.store.models import StoredRun, StoredSnapshot

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


class TestTheThrottleHook:
    """The run endpoint's rate limiter — the one route that costs a model call."""

    class _Counting:
        def __init__(self, *, retry_after: int | None) -> None:
            self.retry_after = retry_after
            self.calls: list[Any] = []

        def consume(self, request: Any) -> int | None:
            self.calls.append(request)
            return self.retry_after

    async def test_a_throttled_run_is_429_with_retry_after(self) -> None:
        view = DjangoAGUIView(
            _registry(), model=TestModel(), throttle=self._Counting(retry_after=7)
        )

        response = await view(_post(_run_input("hi")))

        assert response.status_code == 429
        assert response["Retry-After"] == "7"
        assert json.loads(response.content) == {"error": "rate limited", "retry_after": 7}

    async def test_none_lets_the_run_proceed(self) -> None:
        view = DjangoAGUIView(
            _registry(), model=TestModel(), throttle=self._Counting(retry_after=None)
        )

        response = await view(_post(_run_input("double 5")))

        assert isinstance(response, StreamingHttpResponse)

    async def test_zero_is_a_refusal_not_an_allowance(self) -> None:
        # ``0`` means "denied, but the window resets immediately" — distinct
        # from ``None``, and a falsy check here would have inverted it.
        view = DjangoAGUIView(
            _registry(), model=TestModel(), throttle=self._Counting(retry_after=0)
        )

        response = await view(_post(_run_input("hi")))

        assert response.status_code == 429

    async def test_it_runs_after_authentication(self) -> None:
        """So a limiter can key on the acting user rather than only an IP."""
        throttle = self._Counting(retry_after=None)
        user = SimpleNamespace(is_authenticated=True, pk=7, username="api")
        view = DjangoAGUIView(
            _registry(), model=TestModel(), get_user=lambda _request: user, throttle=throttle
        )

        await _drain(await view(_post(_run_input("double 5"), anonymous=True)))

        assert throttle.calls[0].user is user

    async def test_a_refused_request_is_never_throttled(self) -> None:
        """No quota is spent on a request that was going to be 401 anyway."""
        throttle = self._Counting(retry_after=None)
        view = DjangoAGUIView(_registry(), model=TestModel(), throttle=throttle)

        response = await view(_post(_run_input("hi"), anonymous=True))

        assert response.status_code == 401
        assert throttle.calls == []

    async def test_the_body_is_not_parsed_for_a_throttled_run(self) -> None:
        # A throttled request costs nothing past the auth it already did — an
        # invalid body would otherwise be reported as 400 ahead of the 429.
        view = DjangoAGUIView(
            _registry(), model=TestModel(), throttle=self._Counting(retry_after=3)
        )

        response = await view(_post(b"{not valid json"))

        assert response.status_code == 429

    def test_an_async_consume_is_refused_at_construction(self) -> None:
        """Awaiting it silently would make every request a 429 whose
        ``Retry-After`` is a coroutine — rate-limited rather than
        misconfigured."""

        class _Async:
            async def consume(self, request: Any) -> int | None:
                return None

        with pytest.raises(ImproperlyConfigured, match="async def"):
            DjangoAGUIView(_registry(), model=TestModel(), throttle=_Async())

    async def test_no_throttle_is_the_default(self) -> None:
        view = DjangoAGUIView(_registry(), model=TestModel())

        assert isinstance(await view(_post(_run_input("double 5"))), StreamingHttpResponse)
