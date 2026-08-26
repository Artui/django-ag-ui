from __future__ import annotations

import warnings
from collections.abc import Awaitable, Callable
from typing import Any, cast

from asgiref.sync import markcoroutinefunction, sync_to_async
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
from django_ag_ui.reject_async_throttle import reject_async_throttle
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated


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
    settings when not passed explicitly.

    The agent is built once and reused by every run; ``model_for_request`` and
    ``instructions_for_request`` are the only per-request hooks on it, and
    everything else varies by riding the *run* instead. ``agent_factory`` takes
    over construction wholesale — see [`AGUIServer`][django_ag_ui.AGUIServer] for what
    that turns off.

    **Authentication is the host's responsibility, and the view fails closed.**
    Tools (and the ``drf-mcp`` bridge) act as ``request.user``; if your
    middleware hasn't authenticated the request, that is ``AnonymousUser`` — a
    data-exposure footgun. ``require_authenticated`` therefore defaults to
    **True**: an anonymous request gets a 401 before any agent runs. Pass
    ``require_authenticated=False`` to serve anonymous runs deliberately, and/or
    a ``get_user(request)`` hook to establish the user (e.g. from a token)
    before tools run. ``get_user`` may be **sync or async**; a sync hook runs
    off the event loop, so a plain ORM token lookup is fully supported. A hook
    that raises propagates as an unhandled error (500) — return
    ``AnonymousUser`` (or ``None``) for a clean 401 instead.

    **CSRF:** the view is CSRF-exempt unless told otherwise, because AG-UI
    clients typically authenticate via headers (Bearer / API key), where CSRF
    does not apply. If your deployment authenticates with **session cookies**,
    pass ``csrf_exempt=False`` and send the CSRF token from the client — tools
    act as ``request.user``, so a cookie-auth endpoint without CSRF protection
    lets any third-party page drive the agent as the logged-in user (mitigated,
    not eliminated, by Django's default ``SameSite=Lax`` cookie). Leaving
    ``csrf_exempt`` unset while supplying no ``get_user`` warns at
    construction: that pairing states nothing about how requests authenticate,
    and the likeliest reading is the dangerous one. Any of the three answers
    silences it.
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
        # Enumerable per-request hooks: a hook handed the whole request could
        # read anything off it, which is not a set the agent's reuse can be
        # reasoned about against.
        self._model_for_request = model_for_request
        self._instructions_for_request = instructions_for_request
        self._audit_logger = audit_logger
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
        # A factory, not a shared store: the harness step-store protocol carries
        # no request, so the store binds one at construction and is built fresh
        # per run.
        self._step_store = step_store
        self._deps_factory = deps_factory
        reject_async_throttle(throttle, allows="run")
        self._throttle = throttle
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # Instance state, never module state: two endpoints hold two agents.
        self._built_agent: Agent[AgentDeps, Any] | None = None
        # The warning fires here and only here. Every sibling view resolves the
        # same flag through the same helper, but this is the view a consumer
        # always mounts — the sub-views would raise the identical concern up to
        # six more times.
        warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
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
        Django passes the source run id as ``resume_from``. That run's last
        continuable snapshot seeds this run as ``message_history`` — owner-scoped,
        so another user's run id is a clean 404 — and the client sends only the
        new turn.
        """
        self._warn_if_not_asgi(request)
        # Authenticate before answering *anything* about the route, including
        # which methods it takes. The sibling views already do, and the
        # asymmetry was itself the disclosure: a mount only carries threads/,
        # attachments/, transcribe/, skills/ and runs/ when their backend is
        # configured, so a 405 here against a 404 there told an unauthenticated
        # caller which optional backends a deployment had enabled.
        deny = await self._authorize(request)
        if deny is not None:
            return auth_error_response(deny)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
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
            loaded = await self._load_resume_history(request, resume_from)
            if loaded is None:
                return JsonResponse(
                    {"error": "no resumable run", "run_id": resume_from}, status=404
                )
            message_history, source_thread_id = loaded
            if await self._would_overwrite_another_thread(
                request, source_thread_id, run_input.thread_id
            ):
                return JsonResponse(
                    {
                        "error": "resuming that run would overwrite this thread",
                        "run_id": resume_from,
                        "thread_id": run_input.thread_id,
                    },
                    status=409,
                )
        # The transport ends here: the run's orchestration (adapter, stream
        # composition, persistence, cancel handling) lives on AgentSession.
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

        A ``deps_factory`` takes over entirely; the default binds the acting user
        and the client IP and nothing else. ``request.user`` was materialized off
        the event loop by the auth step, so reading it on the loop is safe, and
        ``getattr`` covers an endpoint served without Django's auth middleware,
        which has no ``user`` attribute at all.
        """
        if self._deps_factory is not None:
            return self._deps_factory(request)
        return AgentDeps(
            user=getattr(request, "user", None),
            # Per-run rather than closed over at construction: one reused agent
            # would otherwise stamp every audit record with the IP of whoever
            # arrived first.
            ip_address=request.META.get("REMOTE_ADDR"),
        )

    def _agent(self) -> Agent[AgentDeps, Any]:
        """This endpoint's agent, built once and reused by every run.

        The invariant the reuse rests on: the agent carries only what the
        constructor fixed, and nothing request-shaped may join it — that rides
        the run instead (``_run_toolsets`` / ``_run_capabilities`` /
        ``_run_model`` / ``_run_instructions``). Instructions stay off it
        even though the endpoint resolves a default, because pydantic-ai treats
        per-run instructions as *additional*, which is a replacement only while
        the agent carries none.

        Lazy rather than eager, so a missing ``MODEL`` surfaces on the first
        request rather than at import.
        """
        if self._built_agent is None:
            self._built_agent = self._build_agent()
        return self._built_agent

    def _build_agent(self) -> Agent[AgentDeps, Any]:
        """Construct this endpoint's agent — see ``_agent`` for the reuse.

        An ``agent_factory`` takes full control of construction. It takes no
        request, so it too is built once.
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
                # No ``audit_ip_address`` here: it is per-request and rides
                # ``AgentDeps`` instead.
                model_settings=config.model_settings,
                retries=config.retries,
                toolsets=list(self._toolsets or []),
                capabilities=capabilities,
                tool_guard=config.tool_guard,
                tool_failure=config.tool_failure,
            ),
        )

    def _registry_names(self) -> set[str]:
        """The ``@tool`` registry's own names — the first claim in the precedence.

        Computed fresh per call rather than cached, because callers mutate the
        set they are handed.
        """
        return {binding.spec.name for binding in self._registry}

    def _claimed_names(self) -> set[str]:
        """Tool names already spoken for, in ``build_tool_catalog``'s precedence.

        Registry → drf-mcp → spec → attachment: each source excludes names an
        earlier one claimed, so a name exposed by two of them cannot reach
        pydantic-ai as a duplicate and raise ``UserError`` mid-run while the
        catalog looks clean. Every name here is fixed at construction — only the
        *objects* are request-bound.

        **This is what a later source excludes, so it is not what the drf-mcp
        bridge starts from** — it already contains the bridge's own names. See
        ``_run_toolsets``.
        """
        seen = self._registry_names()
        if self._drf_mcp_server is not None:
            seen.update(binding.name for binding in self._drf_mcp_server.tools.all())
        return seen

    def _run_toolsets(self, request: HttpRequest) -> list[Any]:
        """The request-bound toolsets this run adds to the agent's own.

        Both close over the live ``request`` — the drf-mcp bridge so the agent
        acts as the logged-in user, the attachment toolset so the model reads
        only that user's files — so neither may live on the reused agent.
        Pydantic-AI takes them as *additional* toolsets for the run.

        The bridge is handed the **registry's** names, not the claimed set: the
        claimed set already folds in the bridge's own registry, so starting
        there made the bridge exclude every tool it exists to expose — a
        ``DRFMCPToolset`` yielding nothing, on every request, with no error
        anywhere while ``GET tools/`` went on advertising them.
        ``_drf_mcp_toolsets`` adds the server's names afterwards, which is what
        the spec and attachment sources below must exclude.
        """
        seen = self._registry_names()
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

        A string from ``model_for_request`` goes through the same ``API_KEY`` /
        ``PROVIDER`` resolution the configured model does, rather than falling
        back to environment inference.
        """
        if self._model_for_request is None:
            return None
        return self._resolve_model_value(self._model_for_request(request))

    def _run_instructions(self, request: HttpRequest) -> str:
        """This request's instructions — always supplied, never on the agent.

        ``instructions_for_request`` wins; otherwise the endpoint's resolved
        default.
        """
        if self._instructions_for_request is not None:
            return self._instructions_for_request(request)
        return self._resolve_instructions()

    def _drf_mcp_toolsets(self, server: Any, request: HttpRequest, seen: set[str]) -> list[Any]:
        """Build the per-request drf-mcp toolset, or ``[]`` when not configured.

        Imported lazily so ``rest_framework_mcp`` stays an optional extra; the
        toolset carries ``request`` so the agent acts as the logged-in user.
        Excludes names already in ``seen`` (registry tools win) and reserves the
        server's whole ``tools.all()`` registry into it — the same source
        ``build_tool_catalog`` dedups against.
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
        ``SpecToolset``'s default extractor reads ``ctx.deps.user`` — which is
        why this is a capability on the reused agent rather than a per-run
        toolset. Excludes names already in ``seen`` (registry + drf-mcp win the
        collision) and reserves the spec names.
        """
        if self._spec_capability is not None:
            # A pre-built capability is attached unfiltered: its tool set is the
            # consumer's, so a collision is refused at construction (see
            # ``_reject_spec_name_collisions``) rather than silently narrowed.
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
        to the acting user, and the capability is keyed on the AG-UI ``run_id``.
        For a resume / fork, ``resume_from`` becomes the new run's
        ``parent_run_id``, so the lineage points back without mutating the
        source. Reserves no tool name, so it takes no part in the ``seen`` dedup.
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
    ) -> tuple[list[Any], str | None] | None:
        """Load a prior run's snapshot, and the thread that run belonged to.

        Returns ``None`` — a 404 at the caller — when no step store is configured
        or the source run has no continuable snapshot for this owner (unknown run
        id, another user's run, or a crash before any provider-valid boundary).
        The store is built per request, so ``continue_run`` is owner-scoped and a
        guessed ``run_id`` reads nothing.

        The source thread rides along because the client does not send it and
        the caller cannot do without it — see
        ``_would_overwrite_another_thread``. It is read only after
        ``continue_run`` has already established that the run is this caller's,
        and never reaches the wire.
        """
        if self._step_store is None:
            return None
        from pydantic_ai_harness.step_persistence import continue_run

        store = self._step_store(request)
        try:
            history = list(await continue_run(store, run_id=resume_from))
        except LookupError:
            return None
        return history, await self._source_thread_id(store, resume_from)

    async def _source_thread_id(self, store: Any, resume_from: str) -> str | None:
        """The thread the source run belonged to, or ``None`` where it cannot matter.

        Only an endpoint that persists conversations has anything a resume could
        overwrite, so a stateless one is not charged the lookup at all. ``None``
        also covers a run recorded without a thread — a run this transport did
        not start — which cannot be attributed either way.
        """
        if isinstance(self._conversation_store, NullConversationStore):
            return None
        record = await store.get_run(run_id=resume_from)
        return None if record is None else record.conversation_id

    async def _would_overwrite_another_thread(
        self, request: HttpRequest, source_thread_id: str | None, thread_id: str
    ) -> bool:
        """Whether seeding ``thread_id`` from that run would destroy what it holds.

        ``runs/`` indexes every run the owner has, across all their threads, but
        a client resumes the one it picked into whatever thread is currently
        open — and a save is a whole-row replace, not an append. So a user
        reading thread B who picks a run belonging to thread A used to end up
        with A's conversation stored under B and B's own turns gone: silent,
        irreversible, and invisible until they reopened B.

        Branching a run into a conversation of its own stays supported, because
        it destroys nothing — the refusal is about *overwriting*, not about
        crossing threads, so it needs both an attributable source thread that
        differs and a target that already holds something.
        """
        if source_thread_id is None or source_thread_id == thread_id:
            return False
        return await self._conversation_store.exists(thread_id, request=request)

    def _attachment_toolsets(
        self, store: AttachmentStore | None, request: HttpRequest, seen: set[str]
    ) -> list[Any]:
        """Build the per-request ``read_attachment`` toolset, or ``[]`` when off.

        Empty when uploads are disabled (the default ``NullAttachmentStore``) or
        when ``read_attachment`` is already claimed by a registry / drf-mcp / spec
        tool — those win, and pydantic-ai would otherwise raise ``UserError`` for
        the duplicate name at run time. The toolset carries ``request`` so the
        model reads only the acting user's files.
        """
        if store is None or isinstance(store, NullAttachmentStore) or "read_attachment" in seen:
            return []
        seen.add("read_attachment")
        # ``inline=`` passed explicitly rather than left to the substrate's
        # defaults. Left off, the two budgets in this package's own config -- what
        # may be uploaded, and what may be read back -- could not be set together,
        # and a file between them uploaded, showed a chip, and came back as a
        # description no matter how often the model asked, with nothing on screen
        # to distinguish that from success.
        return [build_attachment_toolset(store, request, inline=self._config.attachment_inline)]

    async def _throttled(self, request: HttpRequest) -> HttpResponseBase | None:
        """Apply the ``throttle`` hook, or ``None`` when the run may proceed.

        Ordering is load-bearing: **after** authentication so a limiter can key
        on the acting user rather than only an IP, and **before** the body is
        parsed, so a throttled request costs nothing more. The hook is
        synchronous and runs off the event loop, so it may touch the Django cache
        or the ORM.
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

        Returns the status to deny with (``401`` when ``require_authenticated``
        is set and the resolved user is anonymous, ``403`` when the ``authorize``
        predicate rejects an established user), or ``None`` to proceed. The
        resolved user is assigned onto ``request.user`` so tools, the drf-mcp
        bridge and conversation ownership act as it. Without a hook, the
        middleware's lazy ``request.user`` is materialized in a worker thread
        first: touching it on the loop with DB-backed sessions raises
        ``SynchronousOnlyOperation``, and downstream loop-side readers rely on
        the cached resolution.
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

        The synchronous WSGI worker buffers a ``StreamingHttpResponse`` instead
        of streaming it; under ASGI the request is an ``ASGIRequest``.
        ``warnings.warn`` dedupes by (message, category, call site), so this
        fires once rather than per request with no module-level flag.
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

        A ``"provider:name"`` string plus an explicit key or provider builds the
        model with that provider rather than letting Pydantic-AI infer the key
        from the environment; a pre-built ``Model`` instance is used as-is.
        """
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
        return self._audit_logger if self._audit_logger is not None else NullAuditLogger()


__all__ = ["DjangoAGUIView"]
