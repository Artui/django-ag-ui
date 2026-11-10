from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.urls import URLPattern, path
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.agent.types.agent_factory_fn import AgentFactoryFn
from django_spec_agents.integrations.resolve_spec_mapping import resolve_spec_mapping
from django_spec_agents.integrations.types.spec_source import SpecSource
from django_spec_agents.persistence.null_attachment_store import NullAttachmentStore
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from django_spec_agents.persistence.types.attachment_store import AttachmentStore
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.policy.audit.types.audit_logger import AuditLogger
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_agents.utils import AuthorizePredicate, GetUser

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.runs_view import RunsView
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.agent.types.spec_capability_source import SpecCapabilitySource
from django_ag_ui.agent.types.spec_toolset_source import SpecToolsetSource
from django_ag_ui.agent.types.throttle import Throttle
from django_ag_ui.check_no_dotted_paths import check_no_dotted_paths
from django_ag_ui.check_removed_settings import check_removed_settings
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.transcribe_view import TranscribeView
from django_ag_ui.persistence.types.thread_activity_source import ThreadActivitySource
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend
from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView

DEFAULT_NAMESPACE = "ag_ui"


class AGUIServer:
    """One config object that mounts an AG-UI endpoint and its sub-views."""

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
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        skills: SkillRegistry | None = None,
        conversation_store: ConversationStore | None = None,
        thread_activity_source: ThreadActivitySource | None = None,
        step_store: Callable[[HttpRequest], Any] | None = None,
        deps_factory: Callable[[HttpRequest], AgentDeps] | None = None,
        throttle: Throttle | None = None,
        transcribe_throttle: Throttle | None = None,
        attachment_store: AttachmentStore | None = None,
        transcription_backend: TranscriptionBackend | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
        agent_factory: AgentFactoryFn | None = None,
        mcp_server: Any = None,
        service_specs: Mapping[str, Any]
        | SpecSource
        | SpecToolsetSource
        | SpecCapabilitySource
        | None = None,
        provider: Any = None,
        config: AGUIConfig | None = None,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        check_removed_settings()
        # The same mistake in its constructor-shaped form: a name where an object belongs.
        check_no_dotted_paths(
            agent_factory=agent_factory,
            attachment_store=attachment_store,
            audit_logger=audit_logger,
            authorize=authorize,
            capabilities=capabilities,
            conversation_store=conversation_store,
            deps_factory=deps_factory,
            get_user=get_user,
            mcp_server=mcp_server,
            instructions_for_request=instructions_for_request,
            model_for_request=model_for_request,
            provider=provider,
            service_specs=service_specs,
            skills=skills,
            step_store=step_store,
            thread_activity_source=thread_activity_source,
            throttle=throttle,
            toolsets=toolsets,
            transcribe_throttle=transcribe_throttle,
            transcription_backend=transcription_backend,
        )
        self._registry = registry
        self._skills = skills
        self._namespace = namespace
        self._config: AGUIConfig = _with_registry_prompts(
            config if config is not None else build_ag_ui_config(), registry
        )
        self._policy: dict[str, Any] = {
            "require_authenticated": require_authenticated,
            "get_user": get_user,
            "authorize": authorize,
            "csrf_exempt": csrf_exempt,
        }
        self._conversation_store: ConversationStore = (
            conversation_store if conversation_store is not None else NullConversationStore()
        )
        self._thread_activity_source = thread_activity_source
        self._attachment_store: AttachmentStore = (
            attachment_store if attachment_store is not None else NullAttachmentStore()
        )
        self._transcription_backend: TranscriptionBackend = (
            transcription_backend
            if transcription_backend is not None
            else NullTranscriptionBackend()
        )
        self._mcp_server = mcp_server
        self._transcribe_throttle = transcribe_throttle
        self._service_specs, self._spec_capability, self._spec_source = _resolve_spec_source(
            service_specs
        )
        if self._spec_capability is None and self._service_specs:
            _reject_unguarded_specs(self._service_specs)
        if self._spec_capability is not None:
            _reject_spec_name_collisions(self._service_specs or {}, registry, self._mcp_server)
        self._step_store = step_store
        self._deps_factory = deps_factory
        self._view = DjangoAGUIView(
            registry,
            model=model,
            instructions=instructions,
            model_for_request=model_for_request,
            instructions_for_request=instructions_for_request,
            audit_logger=audit_logger,
            toolsets=toolsets,
            capabilities=capabilities,
            agent_factory=agent_factory,
            mcp_server=mcp_server,
            service_specs=self._service_specs,
            spec_capability=self._spec_capability,
            spec_source=self._spec_source,
            provider=provider,
            attachment_store=self._attachment_store,
            conversation_store=self._conversation_store,
            step_store=self._step_store,
            deps_factory=self._deps_factory,
            throttle=throttle,
            config=self._config,
            **self._policy,
        )

    @property
    def urls(self) -> tuple[list[URLPattern], str, str]:
        """The namespaced ``(patterns, app_name, namespace)`` triple ``path()`` mounts."""
        return self._build_patterns(), self._namespace, self._namespace

    def _build_patterns(self) -> list[URLPattern]:
        patterns = [
            path("", self._view, name="endpoint"),
            path(
                "tools/",
                ToolsView(
                    self._registry,
                    mcp_server=self._mcp_server,
                    service_specs=self._service_specs,
                    spec_capability=self._spec_capability,
                    **self._policy,
                ),
                name="tools",
            ),
        ]
        if self._step_store is not None:
            # Two names for one mechanism: ``continue_run`` and ``fork_run`` are data-identical.
            patterns.append(
                path("resume/<str:resume_from>/", self._view, name="resume"),
            )
            patterns.append(
                path("fork/<str:resume_from>/", self._view, name="fork"),
            )
            patterns.append(
                path(
                    "runs/",
                    RunsView(self._step_store, config=self._config, **self._policy),
                    name="runs",
                ),
            )
        if self._skills is not None:
            patterns.append(
                path("skills/", SkillsView(self._skills, **self._policy), name="skills")
            )
        if not isinstance(self._conversation_store, NullConversationStore):
            threads_view = ThreadsView(
                self._conversation_store,
                config=self._config,
                thread_activity_source=self._thread_activity_source,
                **self._policy,
            )
            patterns.append(path("threads/", threads_view, name="threads"))
            patterns.append(path("threads/<str:thread_id>/", threads_view, name="thread"))
        if not isinstance(self._attachment_store, NullAttachmentStore):
            attachments_view = AttachmentsView(
                self._attachment_store, config=self._config, **self._policy
            )
            patterns.append(path("attachments/", attachments_view, name="attachments"))
            patterns.append(
                path("attachments/<str:attachment_id>/", attachments_view, name="attachment")
            )
        if not isinstance(self._transcription_backend, NullTranscriptionBackend):
            transcribe_view = TranscribeView(
                self._transcription_backend,
                config=self._config,
                throttle=self._transcribe_throttle,
                **self._policy,
            )
            patterns.append(path("transcribe/", transcribe_view, name="transcribe"))
        return patterns


def _resolve_spec_source(
    service_specs: Any,
) -> tuple[dict[str, Any] | None, Any, Any]:
    """Split ``service_specs=`` into a mapping, a capability, and the source."""
    if service_specs is None:
        return None, None, None
    toolset = getattr(service_specs, "get_toolset", None)
    if toolset is not None:
        return dict(toolset().specs), service_specs, None
    specs = getattr(service_specs, "specs", None)
    if callable(specs):
        return dict(resolve_spec_mapping(service_specs)), None, service_specs
    if specs is None:
        return dict(service_specs), None, None
    from django_spec_agents.spec_tools import SpecCapability

    return dict(specs), SpecCapability.from_toolset(service_specs), None


def _reject_spec_name_collisions(
    specs: Mapping[str, Any], registry: ToolRegistry, mcp_server: Any
) -> None:
    """Refuse a pre-built toolset whose tool names this endpoint already owns."""
    claimed: dict[str, str] = {binding.spec.name: "the @tool registry" for binding in registry}
    if mcp_server is not None:
        for binding in mcp_server.tools.all():
            claimed.setdefault(binding.name, "the bridged MCP server")
    clashing = sorted(set(specs) & set(claimed))
    if not clashing:
        return
    named = ", ".join(f"{name!r} ({claimed[name]})" for name in clashing)
    raise ImproperlyConfigured(
        f"AGUIServer(service_specs=...) was given a pre-built spec toolset whose "
        f"tool name(s) this endpoint already defines: {named}. "
        "A mapping would have had the colliding names dropped in that source's "
        "favour, but a pre-built toolset is yours and is attached as-is. Rename "
        "on one side, or pass the specs as a mapping to get the old precedence."
    )


def _reject_unguarded_specs(specs: dict[str, Any]) -> None:
    """Refuse a spec that declares no ``permissions`` here, at construction."""
    try:
        from django_service_specs import kernel_spec
    except ImportError as error:
        raise ImportError(
            "AGUIServer(service_specs=...) requires django-service-specs: "
            "install django-ag-ui[specs]"
        ) from error

    unguarded: list[str] = []
    for name, spec in specs.items():
        try:
            compiled = kernel_spec(spec)
        except TypeError as exc:
            raise TypeError(f"AGUIServer(service_specs=...) tool {name!r}: {exc}") from exc
        if compiled.permissions is None:
            unguarded.append(name)
    if not unguarded:
        return
    names = ", ".join(repr(name) for name in sorted(unguarded))
    raise ImproperlyConfigured(
        f"AGUIServer(service_specs=...) was given spec(s) with no "
        f"permissions: {names}. A spec dispatched off HTTP has no view to "
        "supply permissions of its own, so nothing gates these calls and the "
        "model can make any of them. Declare permissions on each spec; a spec "
        "any caller may call says so with permissions=[Unrestricted()]. To "
        "migrate a large registry gradually, attach the capability yourself "
        "instead — capabilities=[SpecCapability(specs, require_permissions=False)] "
        "— which skips this server's tool-catalog registration, so tool-call "
        "cards render unlabelled."
    )


def _with_registry_prompts(config: AGUIConfig, registry: ToolRegistry) -> AGUIConfig:
    """Fold each registry tool's ``confirm=`` into the approval-prompt map."""
    from_registry = {
        binding.spec.name: binding.spec.confirm
        for binding in registry
        if binding.spec.confirm is not None
    }
    if not from_registry:
        return config
    return dataclasses.replace(
        config, approval_prompts={**from_registry, **config.approval_prompts}
    )


__all__ = ["DEFAULT_NAMESPACE", "AGUIServer"]
