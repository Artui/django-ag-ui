from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.http import (
    HttpRequest,
    HttpResponseNotAllowed,
    JsonResponse,
    StreamingHttpResponse,
)
from django.http.response import HttpResponseBase
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.resolve_agent_factory import resolve_agent_factory
from django_ag_ui.agent.resolve_dotted_instances import resolve_dotted_instances
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.conf import get_settings
from django_ag_ui.policy.audit.resolve_audit_logger import resolve_audit_logger
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger
from django_ag_ui.registry.tool_registry import ToolRegistry


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
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        model: Any = None,
        instructions: str | None = None,
        audit_logger: AuditLogger | None = None,
        csrf_exempt: bool = True,
    ) -> None:
        self._registry = registry
        self._model = model
        self._instructions = instructions
        self._audit_logger = audit_logger
        # Django's CsrfViewMiddleware reads this attribute off the view
        # callable. AG-UI clients authenticate via headers/session and post
        # JSON; CSRF is the consumer's call. Default exempt, overridable.
        self.csrf_exempt = csrf_exempt

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        try:
            run_input = AGUIAdapter.build_run_input(request.body)
        except ValidationError as error:
            # Report the count only — the raw error payload echoes the request
            # bytes (not JSON-serialisable, and not something to reflect back).
            return JsonResponse(
                {"error": "invalid RunAgentInput", "error_count": error.error_count()},
                status=400,
            )
        agent = self._build_agent()
        adapter = AGUIAdapter(agent, run_input)
        stream = adapter.encode_stream(adapter.run_stream())
        response = StreamingHttpResponse(stream, content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _build_agent(self) -> Agent[None, Any]:
        """Construct the per-request agent.

        When ``DJANGO_AG_UI['AGENT_FACTORY']`` is set, that callable takes full
        control of construction (the escape hatch). Otherwise the built-in
        :func:`build_agent` wires the registry tools, audited, plus any
        configured ``MODEL_SETTINGS`` / ``RETRIES`` / ``TOOLSETS``.
        """
        settings = get_settings()
        factory = resolve_agent_factory(settings.agent_factory)
        if factory is not None:
            return factory(self._registry, settings)
        return build_agent(
            self._registry,
            AgentConfig(
                model=self._resolve_model(),
                instructions=self._resolve_instructions(),
                audit_logger=self._resolve_audit_logger(),
                model_settings=settings.model_settings,
                retries=settings.retries,
                toolsets=resolve_dotted_instances(settings.toolsets),
                capabilities=resolve_dotted_instances(settings.capabilities),
            ),
        )

    def _resolve_model(self) -> Any:
        if self._model is not None:
            return self._model
        model = get_settings().model
        if model is None:
            raise ImproperlyConfigured(
                "django-ag-ui requires a model: set DJANGO_AG_UI['MODEL'] "
                "(e.g. 'anthropic:claude-sonnet-4.6') or pass model= to "
                "DjangoAGUIView.",
            )
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
