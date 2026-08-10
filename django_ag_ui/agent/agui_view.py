from __future__ import annotations

import warnings
from collections.abc import Awaitable, Callable
from typing import Any, cast

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
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
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
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
from django_ag_ui.agent.types.throttle import Throttle
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

    **The agent is built once and reused by every run** — see :meth:`_agent`.
    Two narrow hooks vary it per request: ``model_for_request(request)`` and
    ``instructions_for_request(request)``, each returning this request's value.
    They are deliberately narrow rather than a request-aware ``agent_factory``:
    everything either can change rides the *run*, so the reuse stays provable,
    and neither costs the ``agent_factory=`` cliff (supplying one turns off the
    drf-mcp bridge, the spec capability, step persistence, the attachment
    toolset, ``MODEL_SETTINGS``, ``RETRIES``, ``toolsets`` and ``capabilities``
    in one go).

    **Authentication is the host's responsibility, and the view fails closed.**
    Tools (and the ``drf-mcp`` bridge) act as ``request.user``; if your
    middleware hasn't authenticated the request, that is ``AnonymousUser`` — a
    data-exposure footgun. ``require_authenticated`` therefore defaults to
    **True**: an anonymous request gets a 401 before any agent runs. Pass
    ``require_authenticated=False`` to serve anonymous runs deliberately, and/or
    a ``get_user(request)`` hook to establish the user (e.g. from a token)
    before tools run. ``get_user`` may be **sync or async**; a sync hook runs
    off the event loop, so a plain ORM token → ``User`` lookup
    (``Token.objects.select_related("user").get(key=...).user``) is fully
    supported. A hook that raises propagates as an unhandled error (500) —
    return ``AnonymousUser`` (or ``None``) for a clean 401 instead.

    **CSRF:** the view is CSRF-exempt unless told otherwise, because AG-UI
    clients typically authenticate via headers (Bearer / API key), where CSRF
    does not apply. If your deployment authenticates with **session cookies**,
    pass ``csrf_exempt=False`` and send the CSRF token from the client — tools
    act as ``request.user``, so a cookie-auth endpoint without CSRF protection
    lets any third-party page drive the agent as the logged-in user (mitigated,
    not eliminated, by Django's default ``SameSite=Lax`` cookie).

    ⚠ **Leaving ``csrf_exempt`` unset while supplying no ``get_user`` warns at
    construction.** That combination says nothing about how requests
    authenticate, and the likeliest reading is the dangerous one: the acting
    user arrives from Django's session cookie, with CSRF turned off. Stating
    either half — ``csrf_exempt=False``, ``csrf_exempt=True``, or a ``get_user``
    hook — settles it and silences the warning. See
    :func:`_warn_if_csrf_unstated`.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        model: Any = None,
        instructions: str | None = None,
        model_for_request: Callable[[HttpRequest], Any] | None = None,
        instructions_for_request: Callable[[HttpRequest], str] | None = None,
        audit_logger: AuditLogger | None = None,
        csrf_exempt: bool | None = None,
        require_authenticated: bool = True,
        get_user: Callable[[HttpRequest], Any]
        | Callable[[HttpRequest], Awaitable[Any]]
        | None = None,
        authorize: AuthorizePredicate | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
        agent_factory: AgentFactoryFn | None = None,
        drf_mcp_server: Any = None,
        service_specs: dict[str, Any] | None = None,
        spec_capability: Any = None,
        provider: Any = None,
        attachment_store: AttachmentStore | None = None,
        conversation_store: ConversationStore | None = None,
        step_store: Callable[[HttpRequest], Any] | None = None,
        deps_factory: Callable[[HttpRequest], AgentDeps] | None = None,
        throttle: Throttle | None = None,
        config: AGUIConfig | None = None,
    ) -> None:
        self._registry = registry
        self._model = model
        self._instructions = instructions
        # Narrow per-request hooks, deliberately narrow: they are the two axes
        # that actually vary per tenant, and keeping them enumerable is what
        # makes the built agent reusable (see ``_agent``). A hook handed the
        # whole request could read anything off it, which is not a set the
        # agent's reuse can be reasoned about against.
        self._model_for_request = model_for_request
        self._instructions_for_request = instructions_for_request
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
        self._spec_capability = spec_capability
        self._provider = provider
        self._attachment_store = attachment_store
        self._conversation_store: ConversationStore = (
            conversation_store if conversation_store is not None else NullConversationStore()
        )
        # A per-request factory (not a shared store): the harness step-store
        # protocol carries no request, so the store binds one at construction and
        # is built fresh per run — see DjangoAGUIView._step_persistence_capabilities.
        self._step_store = step_store
        self._deps_factory = deps_factory
        # Refused at construction rather than awaited per request: see
        # _reject_async_throttle.
        _reject_async_throttle(throttle)
        self._throttle = throttle
        # Scalars, resolved once. Read per request they could only ever be
        # global, so no two endpoints could differ on any of them.
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # The agent, built once and reused. Lazily, so a missing MODEL still
        # surfaces on the first request rather than at import — the same moment
        # it always did. Instance state, never module state: two endpoints hold
        # two agents.
        self._built_agent: Agent[AgentDeps, Any] | None = None
        # Django's CsrfViewMiddleware reads this attribute off the view
        # callable. AG-UI clients authenticate via headers/session and post
        # JSON; CSRF is the consumer's call. Exempt unless stated, overridable —
        # ``None`` means *unstated*, which behaves as exempt but is what lets the
        # guard below tell "header auth, CSRF does not apply" (an explicit True)
        # apart from "nobody decided" (the default).
        _warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = True if csrf_exempt is None else csrf_exempt
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
        throttled = await self._throttled(request)
        if throttled is not None:
            return throttled
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
            self._agent(),
            run_input,
            request,
            model=self._run_model(request),
            instructions=self._run_instructions(request),
            toolsets=self._run_toolsets(request),
            capabilities=self._run_capabilities(request, run_input, resume_from),
            deps=self._build_deps(request),
            audit_logger=self._resolve_audit_logger(),
            config=self._config,
            conversation_store=self._conversation_store,
            message_history=message_history,
        )
        response = StreamingHttpResponse(session.stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _build_deps(self, request: HttpRequest) -> AgentDeps:
        """The per-run dependencies handed to the agent as ``ctx.deps``.

        A ``deps_factory`` takes over entirely, so a project can carry its own
        per-run context (a tenant, a feature-flag snapshot) or seed
        ``AgentDeps.state`` with a Pydantic model — which is the only way to get
        AG-UI's inbound shared state *validated*, since pydantic-ai validates it
        against ``type(deps.state)``.

        The default binds the acting user and nothing else.
        ``request.user`` was already materialized off the event loop by the auth
        step, so reading it on the loop is safe; ``getattr`` because an endpoint
        served without Django's auth middleware has no ``user`` attribute at all
        — the same accessor, and the same "no user is ``None``" rule, that
        ``materialize_request_user`` applies.
        """
        if self._deps_factory is not None:
            return self._deps_factory(request)
        return AgentDeps(
            user=getattr(request, "user", None),
            # Per-run, so it travels with the run rather than with the agent.
            # ``AuditCapability`` reads it off the deps: closed over at
            # construction, one reused agent would stamp every audit record with
            # the IP of whoever arrived first.
            ip_address=request.META.get("REMOTE_ADDR"),
        )

    def _agent(self) -> Agent[AgentDeps, Any]:
        """This endpoint's agent, built once and reused by every run.

        **Building it per request meant re-deriving a JSON Schema for every
        registered tool on every call** — the single most expensive thing the
        endpoint did, repeated to produce a byte-identical result. What made the
        rebuild look necessary was that the agent used to carry request-shaped
        things; it no longer does. They ride the run instead (see
        :meth:`_run_toolsets` / :meth:`_run_capabilities` /
        :meth:`_run_model` / :meth:`_run_instructions`), which is pydantic-ai's
        own seam and the same move ``AgentDeps`` made for the acting user.

        **So what stays here is exactly what the constructor fixed**: the
        registry tools, this endpoint's ``toolsets`` / ``capabilities``, the spec
        capability, model settings, retries, the tool guard, and the audit
        logger. None of it can vary between two requests to the same endpoint —
        which is what makes reuse provable rather than merely plausible. Two
        endpoints are two view instances and two agents.

        **Instructions are deliberately left off the agent**, even though the
        endpoint has a resolved default. They are supplied per run instead, and
        pydantic-ai treats per-run instructions as *additional* — which is
        exactly a replacement when the agent carries none. Baking them in would
        have made ``instructions_for_request`` either impossible or a cache key,
        and a key that a project can vary per user is a key that never hits.

        Lazy rather than eager, so a missing ``MODEL`` still surfaces on the
        first request rather than at import — the same moment it always did.
        """
        if self._built_agent is None:
            self._built_agent = self._build_agent()
        return self._built_agent

    def _build_agent(self) -> Agent[AgentDeps, Any]:
        """Construct this endpoint's agent — see :meth:`_agent` for the reuse.

        When an ``agent_factory`` is passed, that callable takes full control of
        construction (the escape hatch). It takes no request and never did, so it
        is built once like everything else here.
        """
        config = self._config
        if self._agent_factory is not None:
            return self._agent_factory(self._registry, config)
        capabilities = list(self._capabilities or [])
        # The spec path is a capability (not a bare toolset) so its conventions
        # reach the model via ``get_instructions``.
        capabilities += self._spec_capabilities(self._service_specs, self._claimed_names())
        return build_agent(
            self._registry,
            AgentConfig(
                model=self._resolve_model(),
                # Supplied per run — see the docstring above.
                instructions=None,
                audit_logger=self._resolve_audit_logger(),
                # No ``audit_ip_address`` here: it is per-request, and a value
                # closed over at construction would stamp every audit record with
                # the IP of whoever arrived first. It rides ``AgentDeps`` instead.
                model_settings=config.model_settings,
                retries=config.retries,
                toolsets=list(self._toolsets or []),
                capabilities=capabilities,
                tool_guard=config.tool_guard,
            ),
        )

    def _claimed_names(self) -> set[str]:
        """Tool names already spoken for, in ``build_tool_catalog``'s precedence.

        Registry → drf-mcp → spec → attachment: each source excludes names an
        earlier one claimed, so a name exposed by two of them (``DRF_MCP_SERVER``
        and ``SERVICE_SPECS`` both defining ``foo``, or either defining
        ``read_attachment``) can't reach pydantic-ai as a duplicate and raise
        ``UserError`` mid-run while the catalog looks clean.

        **Every one of these names is fixed at construction**, which is what
        lets the spec capability sit on the reused agent while the drf-mcp and
        attachment toolsets are built per run: it is only the *objects* that are
        request-bound, never the name set they are filtered against. Computed
        fresh per call rather than cached, because callers mutate the set they
        are handed.
        """
        seen: set[str] = {binding.spec.name for binding in self._registry}
        if self._drf_mcp_server is not None:
            seen.update(binding.name for binding in self._drf_mcp_server.tools.all())
        return seen

    def _run_toolsets(self, request: HttpRequest) -> list[Any]:
        """The request-bound toolsets this run adds to the agent's own.

        Both close over the live ``request`` — the drf-mcp bridge so the agent
        acts as the logged-in user, the attachment toolset so the model reads
        only that user's files — so neither can live on a reused agent.
        Pydantic-AI takes them as *additional* toolsets for the run, which is
        what lets them stay per-request without the agent following.
        """
        seen = self._claimed_names()
        toolsets = self._drf_mcp_toolsets(self._drf_mcp_server, request, seen)
        # Reserve the spec names between the two, exactly as the catalog's
        # precedence has it, so an attachment tool can't shadow a spec tool.
        seen.update(self._service_specs or {})
        toolsets += self._attachment_toolsets(self._attachment_store, request, seen)
        return toolsets

    def _run_capabilities(
        self, request: HttpRequest, run_input: Any, resume_from: str | None
    ) -> list[Any]:
        """The run-bound capabilities: step persistence, keyed on *this* run."""
        return self._step_persistence_capabilities(request, run_input, resume_from)

    def _run_model(self, request: HttpRequest) -> Any:
        """This request's model, or ``None`` to use the endpoint's own.

        ``model_for_request`` is the narrow per-request hook: a string goes
        through the same ``API_KEY`` / ``PROVIDER`` resolution the configured
        model does, so a hook returning ``"anthropic:…"`` behaves like the
        setting rather than falling back to environment inference.
        """
        if self._model_for_request is None:
            return None
        return self._resolve_model_value(self._model_for_request(request))

    def _run_instructions(self, request: HttpRequest) -> str:
        """This request's instructions — always supplied, never on the agent.

        ``instructions_for_request`` wins; otherwise the endpoint's resolved
        default. Passed per run because the agent carries none, so pydantic-ai's
        "additional instructions" are the whole of them.
        """
        if self._instructions_for_request is not None:
            return self._instructions_for_request(request)
        return self._resolve_instructions()

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

    def _spec_capabilities(self, specs: dict[str, Any] | None, seen: set[str]) -> list[Any]:
        """Build the drf-services `SpecCapability`, or `[]` when unset.

        Imported lazily so `djangorestframework-pydantic-ai` (and drf-services)
        stay an optional `[spec-tools]` extra. The acting user reaches the
        toolset through the run's ``deps``, not a closure over ``request`` —
        ``SpecToolset``'s default extractor reads ``ctx.deps.user`` — and the
        toolset's ``get_instructions`` teaches the model the spec conventions.
        Excludes names already in ``seen`` (registry + drf-mcp win the collision)
        and reserves the spec names so the attachment toolset that follows can't
        shadow one.
        """
        if self._spec_capability is not None:
            # Pre-built: attached as given. ⚠ No ``exclude_names`` filtering —
            # the consumer built this object and its tool set is theirs, so a
            # collision is refused at construction (see
            # ``_reject_spec_name_collisions``) rather than silently narrowed
            # here. The names are still reserved, so the attachment toolset
            # after this one cannot shadow one.
            seen.update(specs or {})
            return [self._spec_capability]
        if specs is None:
            return []
        from django_pydantic_agent.integrations.build_spec_capability import build_spec_capability

        capability = build_spec_capability(specs, exclude_names=frozenset(seen))
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

    async def _throttled(self, request: HttpRequest) -> HttpResponseBase | None:
        """Apply the ``throttle`` hook, or ``None`` when the run may proceed.

        Runs **after** authentication so a limiter can key on the acting user
        rather than only an IP, and **before** the body is parsed or the run
        starts, so a throttled request costs nothing beyond the auth it already
        did.

        The hook is synchronous and runs off the event loop, so it may touch the
        Django cache or the ORM the same way a ``get_user`` hook may.
        """
        if self._throttle is None:
            return None
        retry_after = await sync_to_async(self._throttle.consume, thread_sensitive=True)(request)
        if retry_after is None:
            return None
        response = JsonResponse({"error": "rate limited", "retry_after": retry_after}, status=429)
        response["Retry-After"] = str(retry_after)
        return response

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
        return self._resolve_model_value(model)

    def _resolve_model_value(self, model: Any) -> Any:
        """Apply the credential path to a model value, whatever supplied it.

        Shared by the configured model and by ``model_for_request``, so a hook
        returning ``"anthropic:…"`` is treated exactly like the setting instead
        of quietly falling back to environment inference.
        """
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


def _reject_async_throttle(throttle: Throttle | None) -> None:
    """Refuse an ``async def consume`` at construction, not at request time.

    The endpoint is async, so an awaitable would be easy to accept — but the
    return value feeds an ``is not None`` check, and a coroutine is neither
    ``None`` nor an integer. Awaiting it silently would make every request a
    429 carrying a coroutine as its ``Retry-After``, and the endpoint would look
    rate-limited rather than misconfigured.

    Sync is the right contract regardless: the hook's work is a cache or ORM
    round-trip, which this view already runs off the event loop. The same call
    was settled the same way for the MCP permission and rate-limit hooks, so a
    project protecting both transports writes one kind of limiter.
    """
    if throttle is None or not iscoroutinefunction(throttle.consume):
        return
    raise ImproperlyConfigured(
        f"{type(throttle).__name__}.consume is declared 'async def', but the "
        "throttle hook is synchronous. django-ag-ui runs it off the event "
        "loop, so it may touch the cache or the ORM directly — declare it "
        "'def consume(self, request)' and return the Retry-After seconds, or "
        "None to allow the run."
    )


def _warn_if_csrf_unstated(csrf_exempt: bool | None, get_user: Any) -> None:
    """Warn when nothing in the configuration says how requests authenticate.

    ⚠ **This catches the deployment the ``require_authenticated`` default
    cannot see.** That default refuses *anonymous* callers, which is the whole
    of the protection when nobody is logged in. It says nothing about the case
    where callers *are* authenticated — by session cookie, through Django's
    auth middleware — and the endpoint is CSRF-exempt. There, any third-party
    page can drive the agent as the logged-in user, and every request looks
    perfectly authenticated from the inside.

    ⭐ **Unstated is the signal, not ``True``.** ``csrf_exempt=True`` is a
    defensible choice — it is right for Bearer / API-key clients, where CSRF
    does not apply — so warning on the *value* would fire on a correct
    configuration with no way to say so, which is how a project learns to
    filter the warning. ``None`` (the default) means the question was never
    asked, and that is the only state worth a warning. Any of the three
    answers silences it:

    - ``csrf_exempt=False`` — CSRF enforced; cookie auth is safe.
    - ``csrf_exempt=True`` — deliberately exempt; header-authenticated clients.
    - ``get_user=...`` — the request carries its own credential, so the session
      cookie is not what authenticates it.

    Construction-time rather than per-request, for the same reason
    ``AGUIServer`` refuses unguarded specs at construction: an operator reading
    this in ``urls.py`` is in a different situation from one reading it in
    Sentry a week later. It lives here rather than on ``AGUIServer`` because
    both are import-time paths — nothing about the check needs a request — so
    the view is where it covers ``AGUIServer`` *and* a directly-constructed
    view, with one implementation and one warning.
    """
    if csrf_exempt is not None or get_user is not None:
        return
    warnings.warn(
        "django-ag-ui: this AG-UI endpoint is CSRF-exempt (the default) and has "
        "no get_user hook, so the acting user can only be coming from Django's "
        "session cookie — and tools act as that user, which lets any "
        "third-party page drive the agent as whoever is logged in. Pass "
        "csrf_exempt=False (and send the CSRF token from the client) for "
        "cookie authentication, or csrf_exempt=True to confirm your clients "
        "authenticate by header (Bearer / API key), where CSRF does not apply.",
        RuntimeWarning,
        stacklevel=3,
    )


__all__ = ["DjangoAGUIView"]
