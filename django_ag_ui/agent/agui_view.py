from __future__ import annotations

import warnings
from collections.abc import Awaitable, Callable
from typing import Any, cast

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
from django_pydantic_agent.agent.agent_factory import build_agent
from django_pydantic_agent.agent.attachment_toolset import build_attachment_toolset
from django_pydantic_agent.agent.build_model import build_model
from django_pydantic_agent.agent.types.agent_config import AgentConfig
from django_pydantic_agent.agent.types.agent_factory_fn import AgentFactoryFn
from django_pydantic_agent.persistence.null_attachment_store import NullAttachmentStore
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.persistence.types.attachment_store import AttachmentStore
from django_pydantic_agent.persistence.types.conversation_store import ConversationStore
from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger
from django_pydantic_agent.policy.audit.types.audit_logger import AuditLogger
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from django_pydantic_agent.utils import AuthorizePredicate, aauthorize, auth_error_response
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig


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
        authorize: AuthorizePredicate | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
        agent_factory: AgentFactoryFn | None = None,
        drf_mcp_server: Any = None,
        service_specs: dict[str, Any] | None = None,
        provider: Any = None,
        attachment_store: AttachmentStore | None = None,
        conversation_store: ConversationStore | None = None,
        step_store: Callable[[HttpRequest], Any] | None = None,
        config: AGUIConfig | None = None,
    ) -> None:
        self._registry = registry
        self._model = model
        self._instructions = instructions
        self._audit_logger = audit_logger
        # Collaborators arrive as objects. They used to be dotted paths in
        # settings — an indirection that existed only because settings.py can't
        # hold a live object, and which made it impossible to point two
        # endpoints at different toolsets.
        self._toolsets = toolsets
        self._capabilities = capabilities
        self._agent_factory = agent_factory
        self._drf_mcp_server = drf_mcp_server
        self._service_specs = service_specs
        self._provider = provider
        self._attachment_store = attachment_store
        self._conversation_store: ConversationStore = (
            conversation_store if conversation_store is not None else NullConversationStore()
        )
        # A per-request factory (not a shared store): the harness step-store
        # protocol carries no request, so the store binds one at construction and
        # is built fresh per run — see DjangoAGUIView._step_persistence_capabilities.
        self._step_store = step_store
        # Scalars, resolved once. Read per request they could only ever be
        # global, so no two endpoints could differ on any of them.
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
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

    async def __call__(
        self, request: HttpRequest, *, resume_from: str | None = None
    ) -> HttpResponseBase:
        """Serve a run, optionally resuming / forking from a prior run's snapshot.

        Mounted at the endpoint root for a fresh run, and (when a ``step_store``
        is configured) at ``resume/<run_id>/`` and ``fork/<run_id>/``, where
        Django passes the source run id as ``resume_from``. In that case the
        server loads that run's last continuable snapshot — owner-scoped, so a
        run id belonging to another user is a clean 404 — and seeds this run with
        it as ``message_history``; the client sends only the new turn.
        """
        self._warn_if_not_asgi(request)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        deny = await self._authorize(request)
        if deny is not None:
            return auth_error_response(deny)
        try:
            run_input = AGUIAdapter.build_run_input(request.body)
        except ValidationError as error:
            # Report the count only — the raw error payload echoes the request
            # bytes (not JSON-serialisable, and not something to reflect back).
            return JsonResponse(
                {"error": "invalid RunAgentInput", "error_count": error.error_count()},
                status=400,
            )
        message_history: list[Any] | None = None
        if resume_from is not None:
            message_history = await self._load_resume_history(request, resume_from)
            if message_history is None:
                # No snapshot for this owner + run id (unknown, another user's, or
                # crashed before any provider-valid boundary).
                return JsonResponse(
                    {"error": "no resumable run", "run_id": resume_from}, status=404
                )
        # The transport ends here: the run's orchestration (adapter, stream
        # composition, persistence, cancel handling) lives on AgentSession, so
        # it is testable apart from SSE and swappable under another transport.
        session = AgentSession(
            self._build_agent(request, run_input, resume_from=resume_from),
            run_input,
            request,
            audit_logger=self._resolve_audit_logger(),
            config=self._config,
            conversation_store=self._conversation_store,
            message_history=message_history,
        )
        response = StreamingHttpResponse(session.stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _build_agent(
        self, request: HttpRequest, run_input: Any, *, resume_from: str | None = None
    ) -> Agent[None, Any]:
        """Construct the per-request agent.

        When an ``agent_factory`` is passed, that callable takes full control of
        construction (the escape hatch). Otherwise the built-in
        :func:`build_agent` wires the registry tools, audited, plus this
        endpoint's ``toolsets`` / ``capabilities`` / model settings, the
        per-request ``drf_mcp_server`` toolset, and — when a ``step_store`` is
        configured — a ``StepPersistence`` capability keyed on this run (and, for
        a resume / fork, linked back to ``resume_from`` as its parent run).
        """
        config = self._config
        if self._agent_factory is not None:
            return self._agent_factory(self._registry, config)
        # One name set threaded through every toolset builder so runtime
        # exclusion matches ``build_tool_catalog``'s dedup precedence exactly:
        # registry → drf-mcp → spec → attachment. Each builder excludes names
        # already claimed and reserves its own, so a name exposed by two sources
        # (e.g. ``DRF_MCP_SERVER`` and ``SERVICE_SPECS`` both defining ``foo``, or
        # either defining ``read_attachment``) can't reach pydantic-ai as a
        # duplicate and raise ``UserError`` mid-run while the catalog looks clean.
        seen: set[str] = {binding.spec.name for binding in self._registry}
        toolsets = list(self._toolsets or [])
        toolsets += self._drf_mcp_toolsets(self._drf_mcp_server, request, seen)
        capabilities = list(self._capabilities or [])
        # The spec path is a capability (not a bare toolset) so its conventions
        # reach the model via ``get_instructions`` — but it still reserves its
        # tool names in ``seen`` here, between drf-mcp and the attachment toolset,
        # keeping the ``build_tool_catalog`` dedup precedence unchanged.
        capabilities += self._spec_capabilities(self._service_specs, request, seen)
        capabilities += self._step_persistence_capabilities(request, run_input, resume_from)
        toolsets += self._attachment_toolsets(self._attachment_store, request, seen)
        return build_agent(
            self._registry,
            AgentConfig(
                model=self._resolve_model(),
                instructions=self._resolve_instructions(),
                audit_logger=self._resolve_audit_logger(),
                audit_ip_address=request.META.get("REMOTE_ADDR"),
                model_settings=config.model_settings,
                retries=config.retries,
                toolsets=toolsets,
                capabilities=capabilities,
                tool_guard=config.tool_guard,
            ),
        )

    def _drf_mcp_toolsets(self, server: Any, request: HttpRequest, seen: set[str]) -> list[Any]:
        """Build the per-request drf-mcp toolset, or ``[]`` when not configured.

        Imported lazily so ``rest_framework_mcp`` stays an optional extra; the
        toolset carries ``request`` so the agent acts as the logged-in user.
        Excludes names already in ``seen`` (registry tools win) and reserves the
        server's own tool names into ``seen`` — the full ``server.tools.all()``
        registry, the same source ``build_tool_catalog`` dedups against — so a
        later spec / attachment toolset can't expose a duplicate.
        """
        if server is None:
            return []
        from django_pydantic_agent.integrations.drf_mcp import DRFMCPToolset

        toolset = DRFMCPToolset(server, request, exclude_names=frozenset(seen))
        seen.update(binding.name for binding in server.tools.all())
        return [toolset]

    def _spec_capabilities(
        self, specs: dict[str, Any] | None, request: HttpRequest, seen: set[str]
    ) -> list[Any]:
        """Build the per-request drf-services `SpecCapability`, or `[]` when unset.

        Imported lazily so `djangorestframework-pydantic-ai` (and drf-services)
        stay an optional `[spec-tools]` extra; the capability's toolset carries
        `request` so the agent acts as the logged-in user, and its
        `get_instructions` teaches the model the spec conventions. Excludes names
        already in ``seen`` (registry + drf-mcp win the collision) and reserves
        the spec names so the attachment toolset that follows can't shadow one.
        """
        if specs is None:
            return []
        from django_pydantic_agent.integrations.build_spec_capability import build_spec_capability

        capability = build_spec_capability(specs, request, exclude_names=frozenset(seen))
        seen.update(specs)
        return [capability]

    def _step_persistence_capabilities(
        self, request: HttpRequest, run_input: Any, resume_from: str | None = None
    ) -> list[Any]:
        """Build the per-request harness ``StepPersistence`` capability, or ``[]``.

        Imported lazily so ``pydantic-ai-harness`` stays the optional ``[harness]``
        extra; attached only when a ``step_store`` factory is configured. The
        factory is called with the live ``request`` so the durable ledger scopes
        to the acting user, and the capability is keyed on the AG-UI ``run_id`` so
        the recorded run lines up with the client's run. For a resume / fork,
        ``resume_from`` is recorded as the new run's ``parent_run_id`` so the
        lineage points back at the source without mutating it. Unlike a toolset it
        reserves no tool name, so it takes no part in the ``seen`` dedup.
        """
        if self._step_store is None:
            return []
        from pydantic_ai_harness.step_persistence import StepPersistence

        return [
            StepPersistence(
                store=self._step_store(request),
                run_id=run_input.run_id,
                parent_run_id=resume_from,
            )
        ]

    async def _load_resume_history(
        self, request: HttpRequest, resume_from: str
    ) -> list[Any] | None:
        """Load a prior run's last continuable snapshot as a message history.

        Returns ``None`` — surfaced by the caller as a 404 — when no step store is
        configured, or the source run has no continuable snapshot for this owner
        (unknown run id, another user's run, or a run that crashed before any
        provider-valid boundary). The store is built per request via the factory,
        so ``continue_run`` is owner-scoped: a guessed ``run_id`` reads nothing.
        """
        if self._step_store is None:
            return None
        from pydantic_ai_harness.step_persistence import continue_run

        try:
            return list(await continue_run(self._step_store(request), run_id=resume_from))
        except LookupError:
            return None

    def _attachment_toolsets(
        self, store: AttachmentStore | None, request: HttpRequest, seen: set[str]
    ) -> list[Any]:
        """Build the per-request ``read_attachment`` toolset, or ``[]`` when off.

        Returns an empty list when uploads are disabled (the default
        ``NullAttachmentStore``) or when ``read_attachment`` is already claimed by
        a registry / drf-mcp / spec tool (those win, the same precedence
        ``build_tool_catalog`` applies) — otherwise pydantic-ai raises
        ``UserError`` for the duplicate name at run time. The toolset carries
        ``request`` so the model reads only the acting user's files.
        """
        if store is None or isinstance(store, NullAttachmentStore) or "read_attachment" in seen:
            return []
        seen.add("read_attachment")
        return [build_attachment_toolset(store, request)]

    async def _authorize(self, request: HttpRequest) -> int | None:
        """Establish the user (via ``get_user``) and apply the auth gates.

        Returns the HTTP status the caller should deny with (``401`` when
        ``require_authenticated`` is set and the resolved user is anonymous;
        ``403`` when the ``authorize`` predicate rejects an established user), or
        ``None`` to proceed. A ``get_user`` hook (sync **or** async; sync hooks
        run off the event loop so the ORM is safe) is assigned onto
        ``request.user`` so tools / the drf-mcp bridge / conversation ownership
        act as that user. Without a hook, the middleware's lazy ``request.user``
        is materialized in a worker thread first — touching it on the loop with
        DB-backed sessions raises ``SynchronousOnlyOperation``, and downstream
        loop-side readers (the drf-mcp bridge's ``TokenInfo``, conversation
        ownership) rely on the cached resolution.
        """
        return await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
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
        model = self._model if self._model is not None else self._config.model
        if model is None:
            raise ImproperlyConfigured(
                "django-ag-ui requires a model: set DJANGO_AG_UI['MODEL'] "
                "(e.g. 'anthropic:claude-sonnet-4.6') or pass model= to "
                "AGUIServer / DjangoAGUIView.",
            )
        # A "provider:name" string + an explicit key/provider → build the model
        # with that provider, instead of letting Pydantic-AI infer the key from
        # the environment. A pre-built Model instance is used as-is.
        if isinstance(model, str) and (
            self._config.api_key is not None or self._provider is not None
        ):
            return build_model(model, api_key=self._config.api_key, provider=self._provider)
        return model

    def _resolve_instructions(self) -> str:
        if self._instructions is not None:
            return self._instructions
        return self._config.system_prompt or DEFAULT_SYSTEM_PROMPT

    def _resolve_audit_logger(self) -> AuditLogger:
        # No dotted path to resolve: the logger is passed, or there is none.
        return self._audit_logger if self._audit_logger is not None else NullAuditLogger()


__all__ = ["DjangoAGUIView"]
