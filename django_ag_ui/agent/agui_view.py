from __future__ import annotations

import asyncio
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast

from ag_ui.core import Message
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
from django_spec_agents import IdentifierTooLongError, preflight_identifiers
from django_spec_agents.agent.agent_factory import build_agent
from django_spec_agents.agent.attachment_toolset import build_attachment_toolset
from django_spec_agents.agent.build_model import build_model
from django_spec_agents.agent.types.agent_config import AgentConfig
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.agent.types.agent_factory_fn import AgentFactoryFn
from django_spec_agents.child_sessions.child_lineage_for import child_lineage_for
from django_spec_agents.child_sessions.types.child_session_spend import ChildSessionSpend
from django_spec_agents.child_sessions.types.inbound_queue import InboundQueue
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.null_attachment_store import NullAttachmentStore
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from django_spec_agents.persistence.run_id_reused_error import RunIdReusedError
from django_spec_agents.persistence.types.attachment_store import AttachmentStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_store import RunStore
from django_spec_agents.persistence.utils import owner_id_for
from django_spec_agents.policy.audit.null_audit_logger import NullAuditLogger
from django_spec_agents.policy.audit.types.audit_logger import AuditLogger
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_agents.utils import AuthorizePredicate, aauthorize, auth_error_response
from pydantic import ValidationError
from pydantic_ai import Agent, DeferredToolResults
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.build_a2ui_toolset import build_a2ui_toolset
from django_ag_ui.agent.build_mcp_bridge import build_mcp_bridge
from django_ag_ui.agent.run_owner import RunOwner
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.thread_busy_error import ThreadBusyError
from django_ag_ui.agent.types.throttle import Throttle
from django_ag_ui.agent.utils import IDENTIFIER_TOO_LONG_CODE, a2ui_validator
from django_ag_ui.agent.with_a2ui_action import with_a2ui_action
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.constants import A2UI_RENDER_TOOL_NAME, DETACH_PROP, RUN_LEASE
from django_ag_ui.persistence.utils import messages_from_jsonable
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
    Tools (and the MCP bridge) act as ``request.user``; if your
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
        mcp_server: Any = None,
        service_specs: dict[str, Any] | None = None,
        spec_source: Any = None,
        spec_capability: Any = None,
        provider: Any = None,
        attachment_store: AttachmentStore | None = None,
        conversation_store: ConversationStore | None = None,
        step_store: Callable[[HttpRequest], Any] | None = None,
        deps_factory: Callable[[HttpRequest], AgentDeps] | None = None,
        throttle: Throttle | None = None,
        config: AGUIConfig | None = None,
        run_store: RunStore | None = None,
        agent_id: str | None = None,
        inbound_queue: InboundQueue | None = None,
        child_spend: ChildSessionSpend | None = None,
    ) -> None:
        self._registry = registry
        self._model = model
        self._instructions = instructions
        # Enumerable per-request hooks: a hook handed the whole request could read it all.
        self._model_for_request = model_for_request
        self._instructions_for_request = instructions_for_request
        self._audit_logger = audit_logger
        self._toolsets = toolsets
        self._capabilities = capabilities
        self._agent_factory = agent_factory
        self._mcp_server = mcp_server
        self._service_specs = service_specs
        self._spec_source = spec_source
        self._spec_capability = spec_capability
        self._provider = provider
        self._attachment_store = attachment_store
        self._conversation_store: ConversationStore = (
            conversation_store if conversation_store is not None else NullConversationStore()
        )
        self._step_store = step_store
        self._run_store = run_store
        self._agent_id = agent_id
        self._inbound_queue = inbound_queue
        self._child_spend = child_spend
        self._owners: set[asyncio.Task[Any]] = set()
        self._deps_factory = deps_factory
        reject_async_throttle(throttle, allows="run")
        self._throttle = throttle
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        self._built_agent: Agent[AgentDeps, Any] | None = None
        self._require_a_model()
        if self._config.a2ui_enabled:
            a2ui_validator()
        warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        markcoroutinefunction(cast("Any", self))

    async def __call__(
        self, request: HttpRequest, *, resume_from: str | None = None
    ) -> HttpResponseBase:
        """Serve a run, optionally resuming / forking from a prior run's snapshot."""
        self._warn_if_not_asgi(request)
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
            return JsonResponse(
                {"error": "invalid RunAgentInput", "error_count": error.error_count()},
                status=400,
            )
        if self._config.a2ui_enabled:
            run_input = with_a2ui_action(run_input)
        run_input, a2ui = self._a2ui_run(run_input)
        try:
            prepared = await self._prepare_run(request, run_input, resume_from)
        except IdentifierTooLongError as error:
            return JsonResponse({"error": str(error), "code": IDENTIFIER_TOO_LONG_CODE}, status=400)
        if isinstance(prepared, HttpResponseBase):
            return prepared
        message_history, source_messages = prepared
        lineage = await self._stored_lineage(request, run_input.thread_id)
        session = self._session(
            request,
            run_input,
            resume_from=resume_from,
            a2ui=a2ui,
            message_history=message_history,
            source_messages=source_messages,
            capabilities=lineage,
        )
        owner = session.own(self._owning_store(request))
        refused = await self._claim(owner, request, run_input)
        if refused is not None:
            return refused
        response = StreamingHttpResponse(
            session.stream(owner, detached=self._detaches(request, run_input)),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    async def start_headless(
        self,
        request: HttpRequest,
        run_input: Any,
        *,
        timeout: float,
        prepare: Callable[[Any], Awaitable[tuple[Any, Sequence[Any]]]],
        capabilities: Sequence[Any] = (),
        adopt: bool = False,
        answers: Mapping[str, str] | None = None,
        after_finish: Callable[[RunOutcome], Awaitable[None]] | None = None,
    ) -> RunOwner:
        """Claim the thread (``adopt``: it is already held), ``prepare`` the input, then start."""
        self._preflight_identifiers(request, run_input, None)
        store = self._run_store
        assert store is not None
        thread_id, run_id = run_input.thread_id, run_input.run_id
        if not adopt:
            claim = await store.claim(thread_id, run_id, request=request, lease=RUN_LEASE)
            if not claim.claimed:
                raise ThreadBusyError(thread_id, claim.active_run_id)
        try:
            run_input, prepared = await prepare(run_input)
            capabilities = [*prepared, *capabilities]
            deferred = None if answers is None else DeferredToolResults(calls=dict(answers))
            session = self._session(
                request,
                run_input,
                timeout=timeout,
                capabilities=capabilities,
                deferred_tool_results=deferred,
            )
            owner = session.own(store)
            if after_finish is not None:
                owner.after_finish(after_finish)
            await owner.detach()
        except BaseException:
            await store.finish(thread_id, run_id, RunOutcome.ERROR, request=request)
            raise
        self._keep(owner.start())
        return owner

    def _session(
        self,
        request: HttpRequest,
        run_input: Any,
        *,
        resume_from: str | None = None,
        a2ui: Sequence[Any] = (),
        message_history: list[Any] | None = None,
        source_messages: list[Message] | None = None,
        timeout: float | None = None,
        capabilities: Sequence[Any] = (),
        deferred_tool_results: DeferredToolResults | None = None,
    ) -> AgentSession:
        return AgentSession(
            self._agent(),
            run_input,
            request,
            model=self._run_model(request),
            instructions=self._run_instructions(request),
            toolsets=[*self._run_toolsets(request), *a2ui],
            capabilities=[
                *self._run_capabilities(request, run_input, resume_from),
                *capabilities,
            ],
            deps=self._build_deps(request),
            audit_logger=self._resolve_audit_logger(),
            config=self._config,
            conversation_store=self._conversation_store,
            message_history=message_history,
            source_messages=source_messages,
            timeout=timeout,
            deferred_tool_results=deferred_tool_results,
        )

    def child_capabilities(
        self, conversation: Conversation | None, *, user_joined: bool = False
    ) -> list[Any]:
        """What a run of a child session's thread composes, rebuilt from its stored lineage."""
        if conversation is None or conversation.lineage is None or conversation.parent is None:
            return []
        if self._agent_id is None or self._inbound_queue is None:
            raise ImproperlyConfigured(
                f"thread {conversation.thread_id!r} is a child session, and a run of it "
                "needs its mount's id= and inbound_queue= to keep its parent's bounds"
            )
        return child_lineage_for(
            conversation,
            agent_id=self._agent_id,
            owner=conversation.owner_id or "",
            queue=self._inbound_queue,
            spend=self._child_spend,
            user_joined=user_joined,
        )

    async def _stored_lineage(self, request: HttpRequest, thread_id: str) -> list[Any]:
        if isinstance(self._conversation_store, NullConversationStore):
            return []
        try:
            conversation = await self._conversation_store.load(thread_id, request=request)
        except AnonymousOperationError:
            return []
        return self.child_capabilities(conversation, user_joined=True)

    def _owning_store(self, request: HttpRequest) -> RunStore | None:
        """The run store this request's run is owned through; none for an anonymous run."""
        if self._run_store is None or owner_id_for(request) is None:
            return None
        return self._run_store

    async def _claim(
        self, owner: RunOwner, request: HttpRequest, run_input: Any
    ) -> HttpResponseBase | None:
        """Claim a store-backed run's thread (or 409); under WSGI the response loop starts it."""
        if self._owning_store(request) is None:
            return None
        try:
            claim = await owner.claim()
        except RunIdReusedError:
            return JsonResponse(
                {"error": "that run id was used before", "run_id": run_input.run_id}, status=409
            )
        if not claim.claimed:
            return JsonResponse(
                {
                    "error": "the thread has a run in progress",
                    "thread_id": run_input.thread_id,
                    "run_id": claim.active_run_id,
                },
                status=409,
            )
        if isinstance(request, ASGIRequest):
            self._keep(owner.start())
        return None

    def _detaches(self, request: HttpRequest, run_input: Any) -> bool:
        """Whether the client asked this run to outlive its connection, and it can."""
        forwarded = run_input.forwarded_props
        asked = isinstance(forwarded, dict) and forwarded.get(DETACH_PROP) is True
        can = self._owning_store(request) is not None and bool(self._config.run_timeout_seconds)
        return asked and can

    def _keep(self, task: asyncio.Task[Any]) -> None:
        """Hold the owner's task until it ends; the loop keeps only a weak reference."""
        self._owners.add(task)
        task.add_done_callback(self._owners.discard)

    async def _prepare_run(
        self, request: HttpRequest, run_input: Any, resume_from: str | None
    ) -> tuple[list[Any] | None, list[Message] | None] | HttpResponseBase:
        """Refuse ids the stores cannot hold, then load what a resume seeds the run with."""
        self._preflight_identifiers(request, run_input, resume_from)
        if resume_from is None:
            return None, None
        loaded = await self._load_resume_history(request, resume_from)
        if loaded is None:
            return JsonResponse({"error": "no resumable run", "run_id": resume_from}, status=404)
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
        return message_history, await self._source_messages(request, source_thread_id)

    def _preflight_identifiers(
        self, request: HttpRequest, run_input: Any, resume_from: str | None
    ) -> None:
        """Raise ``IdentifierTooLongError`` before the stream opens, for a store that checks."""
        preflight_identifiers(self._conversation_store, thread_id=run_input.thread_id)
        preflight_identifiers(
            self._owning_store(request), thread_id=run_input.thread_id, run_id=run_input.run_id
        )
        if self._step_store is not None:
            preflight_identifiers(
                self._step_store(request),
                run_id=run_input.run_id,
                conversation_id=run_input.thread_id,
                parent_run_id=resume_from,
            )

    def _build_deps(self, request: HttpRequest) -> AgentDeps:
        """The per-run dependencies handed to the agent as ``ctx.deps``."""
        if self._deps_factory is not None:
            return self._deps_factory(request)
        return AgentDeps(
            user=getattr(request, "user", None),
            ip_address=request.META.get("REMOTE_ADDR"),
            call_context={"request": request},
        )

    def _agent(self) -> Agent[AgentDeps, Any]:
        """This endpoint's agent, built once and reused by every run."""
        if self._built_agent is None:
            self._built_agent = self._build_agent()
        return self._built_agent

    def _build_agent(self) -> Agent[AgentDeps, Any]:
        """Construct this endpoint's agent — see ``_agent`` for the reuse."""
        config = self._config
        if self._agent_factory is not None:
            return self._agent_factory(self._registry, config)
        capabilities = list(self._capabilities or [])
        capabilities += self._spec_capabilities(self._service_specs, self._claimed_names())
        return build_agent(
            self._registry,
            AgentConfig(
                model=self._resolve_model(),
                instructions=None,
                audit_logger=self._resolve_audit_logger(),
                model_settings=config.model_settings,
                retries=config.retries,
                toolsets=list(self._toolsets or []),
                capabilities=capabilities,
                tool_guard=config.tool_guard,
                tool_failure=config.tool_failure,
            ),
        )

    def _registry_names(self) -> set[str]:
        """The ``@tool`` registry's own names — the first claim in the precedence."""
        return {binding.spec.name for binding in self._registry}

    def _claimed_names(self) -> set[str]:
        """Tool names already spoken for, in ``build_tool_catalog``'s precedence."""
        seen = self._registry_names()
        if self._mcp_server is not None:
            seen.update(binding.name for binding in self._mcp_server.tools.all())
        return seen

    def _host_tool_names(self) -> set[str]:
        """Every tool name the host already offers: registry, MCP, specs and function toolsets."""
        names = self._claimed_names() | set(self._service_specs or {})
        for toolset in self._agent().toolsets:
            if isinstance(toolset, FunctionToolset):
                names.update(toolset.tools)
        return names

    def _a2ui_run(self, run_input: Any) -> tuple[Any, list[Any]]:
        """The run's ``render_a2ui`` toolset, and the input without a client tool it replaces."""
        if not self._config.a2ui_enabled or A2UI_RENDER_TOOL_NAME in self._host_tool_names():
            return run_input, []
        toolset = build_a2ui_toolset(run_input)
        if toolset is None:
            return run_input, []
        kept = [tool for tool in run_input.tools if tool.name != A2UI_RENDER_TOOL_NAME]
        return run_input.model_copy(update={"tools": kept}), [toolset]

    def _run_toolsets(self, request: HttpRequest) -> list[Any]:
        """The request-bound toolsets this run adds to the agent's own."""
        seen = self._registry_names()
        toolsets = self._mcp_toolsets(request, seen)
        seen.update(self._service_specs or {})
        toolsets += self._attachment_toolsets(self._attachment_store, request, seen)
        return toolsets

    def _run_capabilities(
        self, request: HttpRequest, run_input: Any, resume_from: str | None
    ) -> list[Any]:
        """The run-bound capabilities: step persistence, keyed on *this* run."""
        return self._step_persistence_capabilities(request, run_input, resume_from)

    def _run_model(self, request: HttpRequest) -> Any:
        """This request's model, or ``None`` to use the endpoint's own."""
        if self._model_for_request is None:
            return None
        return self._resolve_model_value(self._model_for_request(request))

    def _run_instructions(self, request: HttpRequest) -> str:
        """This request's instructions — always supplied, never on the agent."""
        if self._instructions_for_request is not None:
            return self._instructions_for_request(request)
        return self._resolve_instructions()

    def _mcp_toolsets(self, request: HttpRequest, seen: set[str]) -> list[Any]:
        """Build the per-request MCP bridge, or ``[]`` when no server is configured."""
        server = self._mcp_server
        if server is None:
            return []
        toolset = build_mcp_bridge(server, request, exclude_names=seen)
        seen.update(binding.name for binding in server.tools.all())
        return [toolset]

    def _spec_capabilities(self, specs: dict[str, Any] | None, seen: set[str]) -> list[Any]:
        """Build the spec tools' `SpecCapability`, or `[]` when unset."""
        if self._spec_capability is not None:
            seen.update(specs or {})
            return [self._spec_capability]
        if specs is None:
            return []
        from django_spec_agents.integrations.build_spec_capability import build_spec_capability

        capability = build_spec_capability(
            self._spec_source if self._spec_source is not None else specs,
            exclude_names=frozenset(seen),
        )
        seen.update(specs)
        return [capability]

    def _step_persistence_capabilities(
        self, request: HttpRequest, run_input: Any, resume_from: str | None = None
    ) -> list[Any]:
        """Build the per-request harness ``StepPersistence`` capability, or ``[]``."""
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
        """Load a prior run's snapshot, and the thread that run belonged to."""
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
        """The thread the source run belonged to, or ``None`` where it cannot matter."""
        if isinstance(self._conversation_store, NullConversationStore):
            return None
        record = await store.get_run(run_id=resume_from)
        return None if record is None else record.conversation_id

    async def _source_messages(
        self, request: HttpRequest, source_thread_id: str | None
    ) -> list[Message] | None:
        """The source thread's stored conversation, or ``None`` when there is none to read."""
        if source_thread_id is None:
            return None
        try:
            conversation = await self._conversation_store.load(source_thread_id, request=request)
        except AnonymousOperationError:
            return None
        if conversation is None:
            return None
        try:
            return messages_from_jsonable(conversation.messages)
        except ValidationError:
            return None

    async def _would_overwrite_another_thread(
        self, request: HttpRequest, source_thread_id: str | None, thread_id: str
    ) -> bool:
        """Whether seeding ``thread_id`` from that run would destroy what it holds."""
        if source_thread_id is None or source_thread_id == thread_id:
            return False
        return await self._conversation_store.exists(thread_id, request=request)

    def _attachment_toolsets(
        self, store: AttachmentStore | None, request: HttpRequest, seen: set[str]
    ) -> list[Any]:
        """Build the per-request ``read_attachment`` toolset, or ``[]`` when off."""
        if store is None or isinstance(store, NullAttachmentStore) or "read_attachment" in seen:
            return []
        seen.add("read_attachment")
        return [build_attachment_toolset(store, request, inline=self._config.attachment_inline)]

    async def _throttled(self, request: HttpRequest) -> HttpResponseBase | None:
        """Apply the ``throttle`` hook, or ``None`` when the run may proceed."""
        if self._throttle is None:
            return None
        retry_after = await sync_to_async(self._throttle.consume, thread_sensitive=True)(request)
        if retry_after is None:
            return None
        response = JsonResponse({"error": "rate limited", "retry_after": retry_after}, status=429)
        response["Retry-After"] = str(retry_after)
        return response

    async def _authorize(self, request: HttpRequest) -> int | None:
        """Establish the user (via ``get_user``) and apply the auth gates."""
        return await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )

    @staticmethod
    def _warn_if_not_asgi(request: HttpRequest) -> None:
        """Warn once when served over WSGI — SSE can't stream there."""
        if not isinstance(request, ASGIRequest):
            warnings.warn(
                "django-ag-ui: the AG-UI endpoint streams Server-Sent Events, which "
                "require ASGI, but this request is served over WSGI — the response "
                "will buffer instead of streaming. Deploy under an ASGI server "
                "(Daphne / Uvicorn).",
                RuntimeWarning,
                stacklevel=2,
            )

    def _require_a_model(self) -> None:
        """Refuse an endpoint with no model, here rather than one request later."""
        if self._agent_factory is not None or self._model_for_request is not None:
            return
        self._resolve_model()

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
        """Apply the credential path to a model value, whatever supplied it."""
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
