from __future__ import annotations

import json
import time
import warnings
from collections.abc import Awaitable, Callable
from typing import Any, cast

from ag_ui.core import Message
from asgiref.sync import markcoroutinefunction
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIRequest
from django.http import (
    HttpRequest,
    HttpResponseNotAllowed,
    JsonResponse,
    StreamingHttpResponse,
)
from django.http.response import HttpResponseBase
from django.utils.module_loading import import_string
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.attachment_toolset import build_attachment_toolset
from django_ag_ui.agent.build_model import build_model
from django_ag_ui.agent.guarded_stream import guarded_stream
from django_ag_ui.agent.reasoning_filter import drop_reasoning_events
from django_ag_ui.agent.resolve_agent_factory import resolve_agent_factory
from django_ag_ui.agent.resolve_dotted_instances import resolve_dotted_instances
from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.conf import get_settings
from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.null_conversation_store import NullConversationStore
from django_ag_ui.persistence.resolve_attachment_store import resolve_attachment_store
from django_ag_ui.persistence.resolve_conversation_store import resolve_conversation_store
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_store import ConversationStore
from django_ag_ui.persistence.utils import owner_id_for
from django_ag_ui.policy.audit.resolve_audit_logger import resolve_audit_logger
from django_ag_ui.policy.audit.types.audit_event import AuditEvent
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.utils import aauthorize


class DjangoAGUIView:
    """An async Django view that serves an AG-UI endpoint.

    Bridges a Django ``HttpRequest`` to Pydantic-AI's ``AGUIAdapter`` without
    Starlette: it parses the posted ``RunAgentInput``, builds a Pydantic-AI
    ``Agent`` from the server-side tool registry, and returns a
    ``StreamingHttpResponse`` of AG-UI events (Server-Sent Events). Frontend
    tools declared in the request are merged by the adapter automatically.

    The view is a callable instance, so configuration lives on ``self`` and a
    project can mount several with independent registries. ``model``,
    ``instructions``, and ``audit_logger`` fall back to the ``DJANGO_AG_UI``
    settings when not passed explicitly (tests inject a ``TestModel``).

    **Authentication is the host's responsibility.** Tools (and the ``drf-mcp``
    bridge) act as ``request.user``; if your middleware hasn't authenticated the
    request, that is ``AnonymousUser`` — a data-exposure footgun. Pass
    ``require_authenticated=True`` to fail closed (401 for unauthenticated
    requests), and/or a ``get_user(request)`` hook to establish the user (e.g.
    from a token) before tools run. ``get_user`` may be **sync or async**; a
    sync hook runs off the event loop, so a plain ORM token → ``User`` lookup
    (``Token.objects.select_related("user").get(key=...).user``) is fully
    supported. A hook that raises propagates as an unhandled error (500) —
    return ``AnonymousUser`` (or ``None``) for a clean 401 instead.

    **CSRF:** the view defaults to ``csrf_exempt=True`` because AG-UI clients
    typically authenticate via headers (Bearer / API key), where CSRF does not
    apply. If your deployment authenticates with **session cookies**, pass
    ``csrf_exempt=False`` and send the CSRF token from the client — tools act
    as ``request.user``, so a cookie-auth endpoint without CSRF protection
    lets any third-party page drive the agent as the logged-in user
    (mitigated, not eliminated, by Django's default ``SameSite=Lax`` cookie).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        model: Any = None,
        instructions: str | None = None,
        audit_logger: AuditLogger | None = None,
        csrf_exempt: bool = True,
        require_authenticated: bool = False,
        get_user: Callable[[HttpRequest], Any]
        | Callable[[HttpRequest], Awaitable[Any]]
        | None = None,
    ) -> None:
        self._registry = registry
        self._model = model
        self._instructions = instructions
        self._audit_logger = audit_logger
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        # Django's CsrfViewMiddleware reads this attribute off the view
        # callable. AG-UI clients authenticate via headers/session and post
        # JSON; CSRF is the consumer's call. Default exempt, overridable.
        self.csrf_exempt = csrf_exempt
        # Mark this callable instance as a coroutine function so Django's
        # request handler awaits ``__call__`` when the view is mounted. Without
        # it, ``asgiref.iscoroutinefunction(instance)`` is False and the handler
        # treats the async view as sync, returning an unawaited coroutine.
        # (Cast: the helper is typed for functions but runtime-marks any object.)
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        self._warn_if_not_asgi(request)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not await self._authorize(request):
            return JsonResponse({"error": "authentication required"}, status=401)
        try:
            run_input = AGUIAdapter.build_run_input(request.body)
        except ValidationError as error:
            # Report the count only — the raw error payload echoes the request
            # bytes (not JSON-serialisable, and not something to reflect back).
            return JsonResponse(
                {"error": "invalid RunAgentInput", "error_count": error.error_count()},
                status=400,
            )
        agent = self._build_agent(request)
        adapter = AGUIAdapter(agent, run_input)
        on_complete = self._conversation_saver(run_input, request)
        # Composed by hand (rather than ``adapter.run_stream``) so the view
        # keeps a reference to the *native* event stream — the innermost
        # generator, whose context manager owns the provider's streaming
        # request. On client disconnect ``guarded_stream`` closes it
        # explicitly, then persists the partial exchange and audits the
        # cancellation; see the guard's docstring for the two disconnect
        # shapes it handles.
        transcript = RunTranscript()
        native = adapter.run_stream_native()
        events = adapter.transform_stream(native, on_complete=on_complete)
        # A reasoning model's chain-of-thought rides through as AG-UI reasoning
        # events (adapter pass-through). Forward it by default; strip it when the
        # consumer opts out, so the model can reason privately.
        if not get_settings().forward_reasoning:
            events = drop_reasoning_events(events)
        stream = guarded_stream(
            adapter.encode_stream(transcript.observe(events)),
            native_events=native,
            on_cancel=self._cancellation_handler(run_input, request, transcript),
        )
        response = StreamingHttpResponse(stream, content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _conversation_saver(
        self,
        run_input: Any,
        request: HttpRequest,
    ) -> Callable[[Any], Awaitable[None]] | None:
        """Build an ``on_complete`` callback that persists the conversation.

        Returns ``None`` when persistence is off (the default
        ``NullConversationStore``), so the stateless path adds no overhead.
        Otherwise the callback mirrors the run's full message history into the
        configured store when the run finishes streaming.
        """
        save = self._message_saver(run_input, request)
        if save is None:
            return None

        async def _on_complete(result: Any) -> None:
            await save(AGUIAdapter.dump_messages(result.all_messages()))

        return _on_complete

    def _message_saver(
        self,
        run_input: Any,
        request: HttpRequest,
    ) -> Callable[[list[Message]], Awaitable[None]] | None:
        """A closure persisting AG-UI messages to the configured store.

        ``None`` when persistence is off — both the completed-run and the
        cancelled-run paths build their message list and hand it here, so the
        two persist with identical thread/owner scoping.
        """
        store: ConversationStore = resolve_conversation_store(get_settings().conversation_store)
        if isinstance(store, NullConversationStore):
            return None
        thread_id: str = run_input.thread_id
        owner_id = owner_id_for(request)

        async def _save(messages: list[Message]) -> None:
            conversation = Conversation(
                thread_id=thread_id,
                messages=messages,
                owner_id=owner_id,
            )
            await store.save(conversation, request=request)

        return _save

    def _cancellation_handler(
        self,
        run_input: Any,
        request: HttpRequest,
        transcript: RunTranscript,
    ) -> Callable[[], Awaitable[None]]:
        """Build the guard's ``on_cancel``: persist the partial exchange, then audit.

        Persistence mirrors the completed-run shape — the client-sent history
        plus whatever the transcript observed before the disconnect — so a
        durable thread reflects the truncated exchange (matching the client,
        which keeps the partial assistant bubble). The audit record rides the
        existing ``record(AuditEvent)`` surface as a ``tool_name="agent.run"``
        event rather than a new protocol method, so custom loggers keep
        working unchanged; ``duration_ms`` measures run start → cancellation.
        """
        save = self._message_saver(run_input, request)
        audit = self._resolve_audit_logger()
        started = time.perf_counter()
        input_messages: list[Message] = list(run_input.messages)
        run_ref = json.dumps(
            {"run_id": run_input.run_id, "thread_id": run_input.thread_id},
            sort_keys=True,
        )

        async def _on_cancel() -> None:
            if save is not None:
                await save([*input_messages, *transcript.messages()])
            audit.record(
                AuditEvent(
                    tool_name="agent.run",
                    arguments_repr=run_ref,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=False,
                    error="cancelled: client disconnected mid-run",
                ),
            )

        return _on_cancel

    def _build_agent(self, request: HttpRequest) -> Agent[None, Any]:
        """Construct the per-request agent.

        When ``DJANGO_AG_UI['AGENT_FACTORY']`` is set, that callable takes full
        control of construction (the escape hatch). Otherwise the built-in
        :func:`build_agent` wires the registry tools, audited, plus any
        configured ``MODEL_SETTINGS`` / ``RETRIES`` / ``TOOLSETS`` /
        ``CAPABILITIES`` and the per-request ``DRF_MCP_SERVER`` toolset.
        """
        settings = get_settings()
        factory = resolve_agent_factory(settings.agent_factory)
        if factory is not None:
            return factory(self._registry, settings)
        toolsets = resolve_dotted_instances(settings.toolsets)
        toolsets += self._drf_mcp_toolsets(settings.drf_mcp_server, request)
        toolsets += self._attachment_toolsets(settings.attachment_store, request)
        return build_agent(
            self._registry,
            AgentConfig(
                model=self._resolve_model(),
                instructions=self._resolve_instructions(),
                audit_logger=self._resolve_audit_logger(),
                model_settings=settings.model_settings,
                retries=settings.retries,
                toolsets=toolsets,
                capabilities=resolve_dotted_instances(settings.capabilities),
            ),
        )

    def _drf_mcp_toolsets(self, dotted_path: str | None, request: HttpRequest) -> list[Any]:
        """Build the per-request drf-mcp toolset, or ``[]`` when not configured.

        Imported lazily so ``rest_framework_mcp`` stays an optional extra; the
        toolset carries ``request`` so the agent acts as the logged-in user.
        """
        if dotted_path is None:
            return []
        from django_ag_ui.integrations.drf_mcp import DrfMcpToolset

        server = import_string(dotted_path)
        # "Registry tools win" on name collisions — the same rule
        # ``build_tool_catalog`` applies. Without the exclusion, pydantic-ai
        # raises ``UserError`` at run time for the duplicate name: a clean
        # catalog but a broken agent.
        registered = frozenset(binding.spec.name for binding in self._registry)
        return [DrfMcpToolset(server, request, exclude_names=registered)]

    def _attachment_toolsets(self, dotted_path: str | None, request: HttpRequest) -> list[Any]:
        """Build the per-request ``read_attachment`` toolset, or ``[]`` when off.

        Returns an empty list when uploads are disabled (the default
        ``NullAttachmentStore``) or when a registry tool already owns the
        ``read_attachment`` name (registry tools win, the same rule the drf-mcp
        bridge applies) — otherwise pydantic-ai raises ``UserError`` for the
        duplicate name at run time. The toolset carries ``request`` so the model
        reads only the acting user's files.
        """
        store = resolve_attachment_store(dotted_path)
        if isinstance(store, NullAttachmentStore) or "read_attachment" in self._registry:
            return []
        return [build_attachment_toolset(store, request)]

    async def _authorize(self, request: HttpRequest) -> bool:
        """Establish the user (via ``get_user``) and enforce authentication.

        Returns ``False`` only when ``require_authenticated`` is set and the
        resolved user is anonymous — the caller then 401s. A ``get_user`` hook
        (sync **or** async; sync hooks run off the event loop so the ORM is
        safe) is assigned onto ``request.user`` so tools / the drf-mcp bridge /
        conversation ownership act as that user. Without a hook, the
        middleware's lazy ``request.user`` is materialized in a worker thread
        first — touching it on the loop with DB-backed sessions raises
        ``SynchronousOnlyOperation``, and downstream loop-side readers (the
        drf-mcp bridge's ``TokenInfo``, conversation ownership) rely on the
        cached resolution.
        """
        return await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
        )

    @staticmethod
    def _warn_if_not_asgi(request: HttpRequest) -> None:
        """Warn once when served over WSGI — SSE can't stream there.

        The endpoint returns a ``StreamingHttpResponse`` of Server-Sent Events,
        which the synchronous WSGI worker buffers instead of streaming. Under
        ASGI the request is an ``ASGIRequest``. ``warnings.warn`` dedupes by
        (message, category, call site), so this fires once rather than per
        request — no module-level "warned" flag needed.
        """
        if not isinstance(request, ASGIRequest):
            warnings.warn(
                "django-ag-ui: the AG-UI endpoint streams Server-Sent Events, which "
                "require ASGI, but this request is served over WSGI — the response "
                "will buffer instead of streaming. Deploy under an ASGI server "
                "(Daphne / Uvicorn).",
                RuntimeWarning,
                stacklevel=2,
            )

    def _resolve_model(self) -> Any:
        settings = get_settings()
        model = self._model if self._model is not None else settings.model
        if model is None:
            raise ImproperlyConfigured(
                "django-ag-ui requires a model: set DJANGO_AG_UI['MODEL'] "
                "(e.g. 'anthropic:claude-sonnet-4.6') or pass model= to "
                "DjangoAGUIView.",
            )
        # A "provider:name" string + an explicit key/provider → build the model
        # with that provider, instead of letting Pydantic-AI infer the key from
        # the environment. A pre-built Model instance is used as-is.
        if isinstance(model, str) and (
            settings.api_key is not None or settings.provider is not None
        ):
            return build_model(model, api_key=settings.api_key, provider=settings.provider)
        return model

    def _resolve_instructions(self) -> str:
        if self._instructions is not None:
            return self._instructions
        return get_settings().system_prompt or DEFAULT_SYSTEM_PROMPT

    def _resolve_audit_logger(self) -> AuditLogger:
        if self._audit_logger is not None:
            return self._audit_logger
        return resolve_audit_logger(get_settings().audit_logger)


__all__ = ["DjangoAGUIView"]
