from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.urls import URLPattern, path
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.agent.types.agent_factory_fn import AgentFactoryFn
from django_pydantic_agent.integrations.resolve_spec_mapping import resolve_spec_mapping
from django_pydantic_agent.integrations.types.spec_source import SpecSource
from django_pydantic_agent.persistence.null_attachment_store import NullAttachmentStore
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.persistence.types.attachment_store import AttachmentStore
from django_pydantic_agent.persistence.types.conversation_store import ConversationStore
from django_pydantic_agent.policy.audit.types.audit_logger import AuditLogger
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from django_pydantic_agent.utils import AuthorizePredicate, GetUser

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.runs_view import RunsView
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.agent.types.spec_capability_source import SpecCapabilitySource
from django_ag_ui.agent.types.spec_toolset_source import SpecToolsetSource
from django_ag_ui.agent.types.throttle import Throttle
from django_ag_ui.check_removed_settings import check_removed_settings
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.transcribe_view import TranscribeView
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend
from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView

DEFAULT_NAMESPACE = "ag_ui"


class AGUIServer:
    """One config object that mounts an AG-UI endpoint and its sub-views.

    The Django-idiomatic front door for the package — the ``admin.site`` idiom
    and the mirror of drf-mcp's ``MCPServer``. Construct it once with the tool
    registry (plus optional stores / auth), then mount its **namespaced**
    [`urls`][django_ag_ui.AGUIServer.urls] with ``path()``:

        from django_ag_ui import AGUIServer

        agent = AGUIServer(registry, csrf_exempt=False)

        urlpatterns = [
            path("agent/", agent.urls),
        ]
        # reverse("ag_ui:endpoint") · "ag_ui:tools" · "ag_ui:threads" · ...

    The registry is passed **once**: the object builds the agent view
    ([`DjangoAGUIView`][django_ag_ui.DjangoAGUIView]) *and* the read-only
    tool catalog ([`ToolsView`][django_ag_ui.ToolsView]) from it. The
    mount point is the consumer's to choose the Django way, so there is no
    ``prefix=``.

    **What gets mounted.** The agent endpoint (``endpoint``) and its tool catalog
    (``tools``) always mount. The rest mount only when their backend is *active*:

    - ``skills`` — a [`SkillRegistry`][django_ag_ui.SkillRegistry]
      was passed (``skills/``, GET JSON for ``data-skills-url``).
    - ``threads`` / ``thread`` — the conversation store is not a
      ``NullConversationStore`` (``threads/`` + ``threads/<id>/``, the history
      drawer's ``data-threads-url``).
    - ``attachments`` / ``attachment`` — the attachment store is not a
      ``NullAttachmentStore`` (``attachments/`` + ``attachments/<id>/``, the
      composer's ``data-attachments-url``).
    - ``transcribe`` — the transcription backend is not a
      ``NullTranscriptionBackend`` (the mic's ``data-transcribe-url``).
    - ``resume`` / ``fork`` / ``runs`` — a ``step_store`` is configured.

    Collaborators are **passed here or absent**: there is no settings fallback,
    and the keys that once held a dotted path are refused at startup by
    ``check_removed_settings`` rather
    than ignored. Unpassed, each falls back to its ``Null*`` backend, so a bare
    ``AGUIServer(registry)`` serves the agent endpoint and its tool catalog and
    nothing else.

    **Request policy, closed by default.** ``require_authenticated`` /
    ``get_user`` / ``authorize`` / ``csrf_exempt`` are forwarded to **every**
    view this object builds, so one policy governs the whole mount — including
    the write endpoints (attachment upload / delete, thread rename / delete,
    transcribe), which ``csrf_exempt=True`` exempts alongside the run endpoint.
    ``require_authenticated`` defaults to **True**, so a bare
    ``AGUIServer(registry)`` serves nobody who is not logged in; pass
    ``require_authenticated=False`` to serve anonymous runs deliberately. The
    agent view's ``model`` and ``instructions`` fall back to the
    ``DJANGO_AG_UI`` settings when not passed.

    **Anonymous scoping caveat.** With ``require_authenticated=False`` and a
    model-backed store, an anonymous request has no owner id. The reference
    contrib stores refuse anonymous thread / attachment operations unless built
    with ``allow_anonymous=True`` (which buckets per browser session), so leave
    the default in place — or pass a ``get_user`` hook — whenever the store
    persists, rather than relying on owner scoping to isolate anonymous visitors
    from one another.

    **Spec tools.** ``service_specs`` takes a ``name -> spec`` mapping, a spec
    registry (drf-services' ``SpecRegistry``, the single declaration site for a
    project exposing the same specs over several transports), or an
    already-built ``SpecToolset`` / ``SpecCapability``:

        AGUIServer(registry, service_specs=spec_registry.by_tag("public"))
        AGUIServer(registry, service_specs=SpecToolset(SPECS, max_page_size=50))

    A filtered registry view (``by_tag`` / ``subset``) is itself a registry, so
    two endpoints can be given different projections with no shared state. The
    pre-built form is the only way to reach a toolset knob (``max_page_size``, an
    ``exception_map``, a ``build_context`` override, ``require_permissions=False``
    while migrating) without abandoning ``service_specs=`` for ``capabilities=``,
    which the tool catalog never sees; it is attached as itself and its ``specs``
    are read for the catalog, so the powerful form keeps the tool-call card
    labels. The parameter's union covers all four shapes, the last two matched
    structurally because drf-pydantic-ai's own types cannot be named from a
    package that only optionally depends on it. Requires the
    ``django-ag-ui[spec-tools]`` extra.

    **One agent, many runs.** The endpoint builds its agent once and reuses it
    rather than re-deriving every tool's JSON Schema per request.
    ``model_for_request(request)`` and ``instructions_for_request(request)`` are
    the two hooks that vary it — the per-tenant model and the per-tenant system
    prompt — and they ride the *run*, through pydantic-ai's own per-run
    ``model`` / ``instructions``:

        AGUIServer(registry, model_for_request=lambda r: r.tenant.model)

    **Rate limiting.** ``throttle`` takes a
    ``Throttle`` — one ``consume(request)``
    returning the suggested ``Retry-After`` in seconds, or ``None`` to allow the
    run — and applies to the **agent endpoint only**, the one that costs a model
    call per request. It runs after authentication, so a limiter can key on the
    acting user rather than only an IP:

        AGUIServer(registry, throttle=FixedWindowThrottle(max_runs=20, per_seconds=60))

    **Per-run dependencies.** ``deps_factory`` is a ``request -> AgentDeps``
    callable replacing the default, which binds only the acting user and their
    IP. Use it to carry project-specific per-run context on an ``AgentDeps``
    subclass, or to seed ``AgentDeps.state`` with a Pydantic model — the only way
    to have AG-UI's inbound shared state *validated*, since pydantic-ai validates
    it against ``type(deps.state)``. Whatever it returns reaches every tool,
    toolset and capability as ``ctx.deps``.

    **Durable step persistence.** ``step_store`` is a *factory* — a
    ``request -> StepStore`` callable rather than a shared store, because the
    ``pydantic-ai-harness`` step-store protocol carries no request. When set,
    every run attaches a ``StepPersistence`` capability recording an owner-scoped
    run / event / snapshot / tool-effect ledger, and three owner-scoped endpoints
    mount: ``resume/<run_id>/`` and ``fork/<run_id>/`` seed a new run with a prior
    run's last continuable snapshot, and ``runs/`` indexes the user's runs so a
    client can *discover* what it may resume rather than only continuing a run
    whose id it still holds. Pass
    ``DefaultStepStore``
    (its constructor *is* the factory) for the reference model-backed store, or
    any such callable. Requires the ``django-ag-ui[harness]`` extra.

    **Namespacing.** [`urls`][django_ag_ui.AGUIServer.urls] returns the
    ``(patterns, app_name, namespace)``
    triple ``path()`` mounts directly (like ``admin.site.urls`` — no
    ``include()``), so endpoint names are namespaced (``namespace``, default
    ``"ag_ui"``) and multiple mounts don't collide — ``reverse("ag_ui:endpoint")``.
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
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        skills: SkillRegistry | None = None,
        conversation_store: ConversationStore | None = None,
        step_store: Callable[[HttpRequest], Any] | None = None,
        deps_factory: Callable[[HttpRequest], AgentDeps] | None = None,
        throttle: Throttle | None = None,
        attachment_store: AttachmentStore | None = None,
        transcription_backend: TranscriptionBackend | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
        agent_factory: AgentFactoryFn | None = None,
        drf_mcp_server: Any = None,
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
        self._registry = registry
        self._skills = skills
        self._namespace = namespace
        # Scalars, resolved once. Read per request they could only ever be
        # global, so no two mounts could differ on a tool-guard policy, a retry
        # budget or an upload cap.
        self._config: AGUIConfig = _with_registry_prompts(
            config if config is not None else build_ag_ui_config(), registry
        )
        # One dict splatted into every view constructor, so a key added here
        # reaches all of them or fails loudly at construction — a second
        # forwarding path can silently miss one, which is how ``csrf_exempt``
        # once left every write endpoint under CsrfViewMiddleware. Typed ``Any``
        # so the mixed-value dict satisfies each constructor's parameter types.
        self._policy: dict[str, Any] = {
            "require_authenticated": require_authenticated,
            "get_user": get_user,
            "authorize": authorize,
            "csrf_exempt": csrf_exempt,
        }
        self._conversation_store: ConversationStore = (
            conversation_store if conversation_store is not None else NullConversationStore()
        )
        self._attachment_store: AttachmentStore = (
            attachment_store if attachment_store is not None else NullAttachmentStore()
        )
        self._transcription_backend: TranscriptionBackend = (
            transcription_backend
            if transcription_backend is not None
            else NullTranscriptionBackend()
        )
        self._drf_mcp_server = drf_mcp_server
        # Normalised once, here, so the spec capability, the tool catalog and the
        # view's tool-name reservation all see a plain mapping. A registry
        # reaching those unresolved breaks both: ``build_tool_catalog`` calls
        # ``.items()``, and the view's ``seen.update(...)`` would iterate
        # ``RegisteredSpec`` records rather than names, quietly stopping
        # tool-name collision detection.
        self._service_specs, self._spec_capability = _resolve_spec_source(service_specs)
        # Only a mapping is checked. A pre-built toolset ran the same check in
        # its own constructor, and may have been built with
        # ``require_permissions=False`` on purpose — the entire reason for
        # accepting one — so re-checking would take that decision back.
        if self._spec_capability is None and self._service_specs:
            _reject_unguarded_specs(self._service_specs)
        if self._spec_capability is not None:
            _reject_spec_name_collisions(self._service_specs or {}, registry)
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
            drf_mcp_server=drf_mcp_server,
            service_specs=self._service_specs,
            spec_capability=self._spec_capability,
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
        """The namespaced ``(patterns, app_name, namespace)`` triple ``path()`` mounts.

        Mounts directly at any prefix — ``path("agent/", server.urls)``, no
        ``include()`` — exactly like ``admin.site.urls``. Every route name listed
        under "What gets mounted" reverses within the namespace, as
        ``reverse("<namespace>:endpoint")`` and so on.
        """
        return self._build_patterns(), self._namespace, self._namespace

    def _build_patterns(self) -> list[URLPattern]:
        patterns = [
            path("", self._view, name="endpoint"),
            path(
                "tools/",
                ToolsView(
                    self._registry,
                    drf_mcp_server=self._drf_mcp_server,
                    service_specs=self._service_specs,
                    spec_capability=self._spec_capability,
                    **self._policy,
                ),
                name="tools",
            ),
        ]
        if self._step_store is not None:
            # Two names for one mechanism, so a client can speak the intent it
            # means: ``continue_run`` and ``fork_run`` are data-identical in the
            # harness, and both verbs share the agent view, which loads the
            # source run's last snapshot when Django hands it ``resume_from``.
            patterns.append(
                path("resume/<str:resume_from>/", self._view, name="resume"),
            )
            patterns.append(
                path("fork/<str:resume_from>/", self._view, name="fork"),
            )
            patterns.append(
                path("runs/", RunsView(self._step_store, **self._policy), name="runs"),
            )
        if self._skills is not None:
            patterns.append(
                path("skills/", SkillsView(self._skills, **self._policy), name="skills")
            )
        if not isinstance(self._conversation_store, NullConversationStore):
            threads_view = ThreadsView(
                self._conversation_store, config=self._config, **self._policy
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
                self._transcription_backend, config=self._config, **self._policy
            )
            patterns.append(path("transcribe/", transcribe_view, name="transcribe"))
        return patterns


def _resolve_spec_source(
    service_specs: Any,
) -> tuple[dict[str, Any] | None, Any]:
    """Split ``service_specs=`` into a mapping and, if given, a pre-built capability.

    Three shapes go in — a ``name -> spec`` mapping, a ``SpecRegistry``, or an
    already-built ``SpecToolset`` / ``SpecCapability`` — and the same two things
    come out, so everything downstream sees one normalised pair. A pre-built
    object is attached as itself *and* has its ``specs`` extracted, so it still
    feeds the tool catalog and the tool-name dedup.

    The parameter is ``Any`` on purpose while the public one is not: this is the
    sniffing boundary, and each arm is recognised by ``getattr``, which no
    checker can narrow through. Declaring the four-shape union here would only
    make the resolved arms unassignable to what each branch calls.
    """
    if service_specs is None:
        return None, None
    toolset = getattr(service_specs, "get_toolset", None)
    if toolset is not None:
        # A ``SpecCapability``: attach it as given, read its names off the
        # toolset it owns.
        return dict(toolset().specs), service_specs
    specs = getattr(service_specs, "specs", None)
    if callable(specs) or specs is None:
        # A plain mapping or a ``SpecRegistry`` — ``SpecRegistry.specs`` is a
        # *method*, which is what tells the two apart from a built toolset
        # whose ``specs`` is a property.
        return dict(resolve_spec_mapping(service_specs)), None
    # A bare ``SpecToolset``. Wrapped so the endpoint composes capabilities
    # uniformly, and so ``defer_loading`` stays available; ``from_toolset``
    # adopts the toolset's own id and instructions, changing nothing about it.
    from rest_framework_pydantic_ai import SpecCapability

    return dict(specs), SpecCapability.from_toolset(service_specs)


def _reject_spec_name_collisions(specs: Mapping[str, Any], registry: ToolRegistry) -> None:
    """Refuse a pre-built toolset whose tool names the registry already owns.

    For a mapping the endpoint drops colliding names and lets the registry win;
    a pre-built toolset is the consumer's and cannot be filtered, so the
    collision has to fail loudly here. Left alone, pydantic-ai raises
    ``UserError`` for the duplicate **mid-run**, long after the catalog looked
    clean.
    """
    clashing = sorted(set(specs) & {binding.spec.name for binding in registry})
    if not clashing:
        return
    raise ImproperlyConfigured(
        f"AGUIServer(service_specs=...) was given a pre-built spec toolset whose "
        f"tool name(s) the @tool registry already defines: {', '.join(repr(n) for n in clashing)}. "
        "A mapping would have had the colliding names dropped in the registry's "
        "favour, but a pre-built toolset is yours and is attached as-is. Rename "
        "on one side, or pass the specs as a mapping to get the old precedence."
    )


def _reject_unguarded_specs(specs: dict[str, Any]) -> None:
    """Refuse a spec with no ``permission_classes`` here, at construction.

    ``permission_classes=None`` means *inherit* over HTTP — the viewset's own
    classes, then DRF's ``DEFAULT_PERMISSION_CLASSES``. Off HTTP neither exists,
    so a spec correctly guarded behind a viewset, with passing HTTP tests,
    becomes callable by whatever the model decides to call.

    ``SpecCapability`` refuses the same thing, but this transport builds its
    capability per request (it needs the request-scoped ``seen`` set for
    tool-name dedup), so the upstream refusal would surface as a 500 on the
    first agent call rather than as a failure to start. Constructing a
    ``DjangoAGUIView`` directly still fails per request.

    Imported lazily: ``djangorestframework-services`` arrives with the
    ``[spec-tools]`` extra, and ``service_specs`` being set is what proves it is
    installed.
    """
    from rest_framework_services import unguarded_specs

    unguarded = unguarded_specs(specs)
    if not unguarded:
        return
    names = ", ".join(repr(name) for name in sorted(unguarded))
    raise ImproperlyConfigured(
        f"AGUIServer(service_specs=...) was given spec(s) with no "
        f"permission_classes: {names}. A spec dispatched off HTTP has no "
        "viewset and no DEFAULT_PERMISSION_CLASSES to inherit from, so nothing "
        "gates these calls and the model can make any of them. Set "
        "spec.permission_classes on each. To migrate a large registry "
        "gradually, attach the capability yourself instead — "
        "capabilities=[SpecCapability(specs, require_permissions=False)] — "
        "which skips this server's tool-catalog registration, so tool-call "
        "cards render unlabelled."
    )


def _with_registry_prompts(config: AGUIConfig, registry: ToolRegistry) -> AGUIConfig:
    """Fold each registry tool's ``confirm=`` into the approval-prompt map.

    A destructive registry tool already carries a human-readable question: the
    ``confirm=`` that reaches the browser as ``x-confirm`` and is what the
    client-side confirmation card asks. When the *server* gates the same tool
    instead, the question should not silently become the serialized call, so the
    two gates read from one source.

    Resolved once, at construction, alongside the rest of the scalars — and the
    project's own ``APPROVAL_PROMPTS`` wins, so a tool's default question can be
    overridden per endpoint without touching the tool.
    """
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
