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
from django.utils.module_loading import import_string
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.attachment_toolset import build_attachment_toolset
from django_ag_ui.agent.build_model import build_model
from django_ag_ui.agent.resolve_agent_factory import resolve_agent_factory
from django_ag_ui.agent.resolve_dotted_instances import resolve_dotted_instances
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.conf import get_settings
from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.resolve_attachment_store import resolve_attachment_store
from django_ag_ui.policy.audit.resolve_audit_logger import resolve_audit_logger
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.utils import AuthorizePredicate, aauthorize, auth_error_response


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
    ) -> None:
        self._registry = registry
        self._model = model
        self._instructions = instructions
        self._audit_logger = audit_logger
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

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
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
        # The transport ends here: the run's orchestration (adapter, stream
        # composition, persistence, cancel handling) lives on AgentSession, so
        # it is testable apart from SSE and swappable under another transport.
        session = AgentSession(
            self._build_agent(request),
            run_input,
            request,
            audit_logger=self._resolve_audit_logger(),
        )
        response = StreamingHttpResponse(session.stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

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
        # One name set threaded through every toolset builder so runtime
        # exclusion matches ``build_tool_catalog``'s dedup precedence exactly:
        # registry → drf-mcp → spec → attachment. Each builder excludes names
        # already claimed and reserves its own, so a name exposed by two sources
        # (e.g. ``DRF_MCP_SERVER`` and ``SERVICE_SPECS`` both defining ``foo``, or
        # either defining ``read_attachment``) can't reach pydantic-ai as a
        # duplicate and raise ``UserError`` mid-run while the catalog looks clean.
        seen: set[str] = {binding.spec.name for binding in self._registry}
        toolsets = resolve_dotted_instances(settings.toolsets)
        toolsets += self._drf_mcp_toolsets(settings.drf_mcp_server, request, seen)
        capabilities = resolve_dotted_instances(settings.capabilities)
        # The spec path is a capability (not a bare toolset) so its conventions
        # reach the model via ``get_instructions`` — but it still reserves its
        # tool names in ``seen`` here, between drf-mcp and the attachment toolset,
        # keeping the ``build_tool_catalog`` dedup precedence unchanged.
        capabilities += self._spec_capabilities(settings.service_specs, request, seen)
        toolsets += self._attachment_toolsets(settings.attachment_store, request, seen)
        return build_agent(
            self._registry,
            AgentConfig(
                model=self._resolve_model(),
                instructions=self._resolve_instructions(),
                audit_logger=self._resolve_audit_logger(),
                audit_ip_address=request.META.get("REMOTE_ADDR"),
                model_settings=settings.model_settings,
                retries=settings.retries,
                toolsets=toolsets,
                capabilities=capabilities,
            ),
        )

    def _drf_mcp_toolsets(
        self, dotted_path: str | None, request: HttpRequest, seen: set[str]
    ) -> list[Any]:
        """Build the per-request drf-mcp toolset, or ``[]`` when not configured.

        Imported lazily so ``rest_framework_mcp`` stays an optional extra; the
        toolset carries ``request`` so the agent acts as the logged-in user.
        Excludes names already in ``seen`` (registry tools win) and reserves the
        server's own tool names into ``seen`` — the full ``server.tools.all()``
        registry, the same source ``build_tool_catalog`` dedups against — so a
        later spec / attachment toolset can't expose a duplicate.
        """
        if dotted_path is None:
            return []
        from django_ag_ui.integrations.drf_mcp import DRFMCPToolset

        server = import_string(dotted_path)
        toolset = DRFMCPToolset(server, request, exclude_names=frozenset(seen))
        seen.update(binding.name for binding in server.tools.all())
        return [toolset]

    def _spec_capabilities(
        self, dotted_path: str | None, request: HttpRequest, seen: set[str]
    ) -> list[Any]:
        """Build the per-request drf-services `SpecCapability`, or `[]` when unset.

        Imported lazily so `djangorestframework-pydantic-ai` (and drf-services)
        stay an optional `[spec-tools]` extra; the capability's toolset carries
        `request` so the agent acts as the logged-in user, and its
        `get_instructions` teaches the model the spec conventions. Excludes names
        already in ``seen`` (registry + drf-mcp win the collision) and reserves
        the spec names so the attachment toolset that follows can't shadow one.
        """
        if dotted_path is None:
            return []
        from django_ag_ui.integrations.build_spec_capability import build_spec_capability

        specs = import_string(dotted_path)
        capability = build_spec_capability(specs, request, exclude_names=frozenset(seen))
        seen.update(specs)
        return [capability]

    def _attachment_toolsets(
        self, dotted_path: str | None, request: HttpRequest, seen: set[str]
    ) -> list[Any]:
        """Build the per-request ``read_attachment`` toolset, or ``[]`` when off.

        Returns an empty list when uploads are disabled (the default
        ``NullAttachmentStore``) or when ``read_attachment`` is already claimed by
        a registry / drf-mcp / spec tool (those win, the same precedence
        ``build_tool_catalog`` applies) — otherwise pydantic-ai raises
        ``UserError`` for the duplicate name at run time. The toolset carries
        ``request`` so the model reads only the acting user's files.
        """
        store = resolve_attachment_store(dotted_path)
        if isinstance(store, NullAttachmentStore) or "read_attachment" in seen:
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
