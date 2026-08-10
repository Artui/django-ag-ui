from __future__ import annotations

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
    :attr:`urls` with ``include()``::

        from django_ag_ui import AGUIServer

        agent = AGUIServer(registry, require_authenticated=True)

        urlpatterns = [
            path("agent/", agent.urls),
        ]
        # reverse("ag_ui:endpoint") · "ag_ui:tools" · "ag_ui:threads" · ...

    The registry is passed **once**: the object builds the agent view
    (:class:`~django_ag_ui.agent.agui_view.DjangoAGUIView`) *and* the read-only
    tool catalog (:class:`~django_ag_ui.agent.tools_view.ToolsView`) from it — no
    ``tools=registry`` echo. The mount point is the consumer's to choose the
    Django way (``path("<prefix>", agent.urls)``); there is no ``prefix=``.

    **What gets mounted.** The agent endpoint (``endpoint``) and its tool catalog
    (``tools``) always mount. The persistence sub-views mount only when their
    backend is *active*:

    - ``skills`` — when a :class:`~django_ag_ui.skills.skill_registry.SkillRegistry`
      is passed (``skills/``, GET JSON for ``data-skills-url``).
    - ``threads`` / ``thread`` — when the conversation store is not a
      :class:`~django_pydantic_agent.persistence.null_conversation_store.NullConversationStore`
      (``threads/`` + ``threads/<id>/`` for the history drawer's ``data-threads-url``).
    - ``attachments`` / ``attachment`` — when the attachment store is not a
      :class:`~django_pydantic_agent.persistence.null_attachment_store.NullAttachmentStore`
      (``attachments/`` + ``attachments/<id>/`` for the composer's ``data-attachments-url``).
    - ``transcribe`` — when the transcription backend is not a
      :class:`~django_ag_ui.persistence.null_transcription_backend.NullTranscriptionBackend`
      (``transcribe/`` for the mic's ``data-transcribe-url``).
    - ``resume`` / ``fork`` / ``runs`` — when a ``step_store`` is configured
      (``resume/<run_id>/`` + ``fork/<run_id>/`` seed a new run from a prior run's
      last continuable snapshot; ``runs/`` indexes what may be resumed).

    ``conversation_store`` / ``attachment_store`` / ``transcription_backend``
    default to the ``DJANGO_AG_UI`` settings-resolved backend (the same one the
    agent view persists to), so configuring a store in settings mounts its
    sub-view automatically; pass an instance to override. Since the defaults
    resolve to the ``Null*`` backends, a bare ``AGUIServer(registry)`` mounts only
    the agent endpoint and its tool catalog — the same surface the old
    ``get_urls(view)`` produced.

    **Authentication seam.** ``require_authenticated`` / ``get_user`` /
    ``authorize`` are forwarded to **every** view this object builds — the agent
    endpoint and all sub-views — so one policy locks down the whole mount
    (``401`` for anonymous when ``require_authenticated``, ``403`` from an
    ``authorize`` predicate, ``get_user`` establishing the acting user). The
    agent view's ``model`` / ``instructions`` / ``audit_logger`` / ``csrf_exempt``
    fall back to settings when not passed. Everything defaults open for
    backwards compatibility; the endpoints are unauthenticated until you set
    these.

    **Anonymous scoping caveat.** With the endpoints left open (the default) and
    a model-backed store, an anonymous request has no owner id. The reference
    contrib stores refuse anonymous thread / attachment operations unless
    ``ALLOW_ANONYMOUS`` is set (in which case they bucket per browser session) —
    so pass ``require_authenticated=True`` (or a ``get_user`` hook) whenever the
    store persists, rather than relying on owner scoping to isolate anonymous
    visitors from one another.

    **Spec tools.** ``service_specs`` takes a ``name -> spec`` mapping *or* a
    spec registry — drf-services 0.27's ``SpecRegistry``, the single declaration
    site for a project exposing the same specs over more than one transport, so
    this endpoint reads the same source an MCP server and the HTTP views do::

        AGUIServer(registry, service_specs=spec_registry.by_tag("public"))

    Either shape is normalised **once, here**, into a plain dict; a filtered view
    (``by_tag`` / ``subset``) is itself a registry, so two endpoints can be given
    different projections with no shared state. Requires the
    ``django-ag-ui[spec-tools]`` extra.

    **Per-run dependencies.** ``deps_factory`` is a ``request -> AgentDeps``
    callable replacing the default, which binds only the acting user. Use it to
    carry project-specific per-run context (a tenant, a feature-flag snapshot)
    on an ``AgentDeps`` subclass, or to seed ``AgentDeps.state`` with a Pydantic
    model — the only way to have AG-UI's inbound shared state *validated*, since
    pydantic-ai validates it against ``type(deps.state)``. Whatever it returns
    reaches every tool, toolset and capability as ``ctx.deps``.

    **Durable step persistence.** ``step_store`` is a *factory* — a
    ``request -> StepStore`` callable, not a shared store instance, because the
    ``pydantic-ai-harness`` step-store protocol carries no request, so the store
    binds one and is built per run. When set, every run attaches a
    ``StepPersistence`` capability that records an owner-scoped run / event /
    snapshot / tool-effect ledger through that store. Pass
    :class:`~django_pydantic_agent.contrib.store.default_step_store.DefaultStepStore` (its
    constructor *is* the ``request -> StepStore`` factory) for the reference
    model-backed store, or any such callable. Requires the
    ``django-ag-ui[harness]`` extra. Configuring it also mounts three owner-scoped
    endpoints: ``resume/<run_id>/`` and ``fork/<run_id>/``, which seed a new run
    with a prior run's last continuable snapshot, and ``runs/``, which indexes the
    user's runs so a client can *discover* what it may resume. Without that index
    a client can only continue a run whose id it still holds — ruling out
    resuming after a page reload or from another device, which is most of what
    durable persistence is for.

    **Namespacing.** :attr:`urls` returns the ``(patterns, app_name, namespace)``
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
        audit_logger: AuditLogger | None = None,
        csrf_exempt: bool = True,
        require_authenticated: bool = False,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        skills: SkillRegistry | None = None,
        conversation_store: ConversationStore | None = None,
        step_store: Callable[[HttpRequest], Any] | None = None,
        deps_factory: Callable[[HttpRequest], AgentDeps] | None = None,
        attachment_store: AttachmentStore | None = None,
        transcription_backend: TranscriptionBackend | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
        agent_factory: AgentFactoryFn | None = None,
        drf_mcp_server: Any = None,
        service_specs: Mapping[str, Any] | SpecSource | None = None,
        provider: Any = None,
        config: AGUIConfig | None = None,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        check_removed_settings()
        self._registry = registry
        self._skills = skills
        self._namespace = namespace
        # Scalars, resolved once — not on every request, where they could only
        # ever be global. This is what lets /internal/agent and /public/agent
        # hold different tool-guard policies, retry budgets, upload caps.
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        # The auth policy shared by every view this object builds — splatted into
        # each constructor. Typed ``Any`` so the mixed-value dict satisfies each
        # constructor's specific parameter types.
        self._auth: dict[str, Any] = {
            "require_authenticated": require_authenticated,
            "get_user": get_user,
            "authorize": authorize,
        }
        # Collaborators are passed, never resolved from a dotted path. The
        # indirection existed only because settings.py can't hold a live object;
        # urls.py can, so `drf_mcp_server=internal_mcp` is now expressible at all
        # — with one global dotted path it was not.
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
        # Normalised **once, here**, so everything downstream sees a plain
        # mapping: the spec capability, the tool catalog, and the view's
        # tool-name reservation. A registry reaching those unresolved fails
        # three different ways — ``build_tool_catalog`` calls ``.items()``
        # (AttributeError), and the view's ``seen.update(...)`` iterates, which
        # yields ``RegisteredSpec`` records rather than names and would quietly
        # stop detecting tool-name collisions.
        self._service_specs, self._spec_capability = _resolve_spec_source(service_specs)
        # ⚠ Only a mapping is checked here. A pre-built toolset ran the same
        # check in its own constructor — and may have been built with
        # ``require_permissions=False`` on purpose, which is the entire reason
        # for accepting one. Re-checking would take that decision back.
        if self._spec_capability is None and self._service_specs:
            _reject_unguarded_specs(self._service_specs)
        if self._spec_capability is not None:
            _reject_spec_name_collisions(self._service_specs or {}, registry)
        # A per-request factory, not a shared store (the harness step-store
        # protocol carries no request). Retained on the object because the
        # resume/fork endpoints a later release mounts will need it too.
        self._step_store = step_store
        self._deps_factory = deps_factory
        self._view = DjangoAGUIView(
            registry,
            model=model,
            instructions=instructions,
            audit_logger=audit_logger,
            csrf_exempt=csrf_exempt,
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
            config=self._config,
            **self._auth,
        )

    @property
    def urls(self) -> tuple[list[URLPattern], str, str]:
        """The namespaced ``(patterns, app_name, namespace)`` triple ``path()`` mounts.

        Mounts directly at any prefix — ``path("agent/", server.urls)``, no
        ``include()`` — exactly like ``admin.site.urls``. Reverse the endpoints
        within the namespace: ``reverse("<namespace>:endpoint")``,
        ``"<namespace>:tools"``, ``"<namespace>:skills"``, ``"<namespace>:threads"``,
        ``"<namespace>:thread"``, ``"<namespace>:attachments"``,
        ``"<namespace>:attachment"``, ``"<namespace>:transcribe"``,
        ``"<namespace>:resume"``, ``"<namespace>:fork"``.
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
                    **self._auth,
                ),
                name="tools",
            ),
        ]
        if self._step_store is not None:
            # Both verbs share the agent view, which loads the source run's last
            # snapshot as message_history when Django hands it ``resume_from``.
            # ``continue_run`` and ``fork_run`` are data-identical in the harness;
            # the endpoints are two names for one mechanism — seed a new run from a
            # prior run's checkpoint — so a client can speak the intent it means.
            patterns.append(
                path("resume/<str:resume_from>/", self._view, name="resume"),
            )
            patterns.append(
                path("fork/<str:resume_from>/", self._view, name="fork"),
            )
            # The discovery half: both verbs address a run *by id*, so without
            # an index a client can only continue a run whose id it still holds
            # — ruling out resuming after a reload or from another device.
            patterns.append(
                path("runs/", RunsView(self._step_store, **self._auth), name="runs"),
            )
        if self._skills is not None:
            patterns.append(path("skills/", SkillsView(self._skills, **self._auth), name="skills"))
        if not isinstance(self._conversation_store, NullConversationStore):
            threads_view = ThreadsView(self._conversation_store, config=self._config, **self._auth)
            patterns.append(path("threads/", threads_view, name="threads"))
            patterns.append(path("threads/<str:thread_id>/", threads_view, name="thread"))
        if not isinstance(self._attachment_store, NullAttachmentStore):
            attachments_view = AttachmentsView(
                self._attachment_store, config=self._config, **self._auth
            )
            patterns.append(path("attachments/", attachments_view, name="attachments"))
            patterns.append(
                path("attachments/<str:attachment_id>/", attachments_view, name="attachment")
            )
        if not isinstance(self._transcription_backend, NullTranscriptionBackend):
            transcribe_view = TranscribeView(
                self._transcription_backend, config=self._config, **self._auth
            )
            patterns.append(path("transcribe/", transcribe_view, name="transcribe"))
        return patterns


def _resolve_spec_source(
    service_specs: Any,
) -> tuple[dict[str, Any] | None, Any]:
    """Split ``service_specs=`` into a mapping and, if given, a pre-built capability.

    Three shapes go in — a ``name -> spec`` mapping, a ``SpecRegistry``, or an
    already-built ``SpecToolset`` / ``SpecCapability`` — and the same two things
    come out, so everything downstream sees one normalised pair.

    ⚠ **Accepting a pre-built object is what makes every ``SpecToolset`` knob
    reachable from here.** ``service_specs=`` could only ever pass the mapping,
    so a project needing ``max_page_size``, an ``exception_map`` or a
    ``build_context`` override had to abandon it for ``capabilities=`` — and
    that path is not wired into the tool catalog, so its tool-call cards render
    unlabelled. One escape hatch cost the other.

    ⭐ **The mapping is still extracted, and that is the point.** A pre-built
    toolset is attached *as itself*, but its ``specs`` also feed the tool
    catalog and the tool-name dedup, so choosing the powerful form no longer
    costs the labels.
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

    ⚠ **A pre-built toolset cannot be filtered, which is exactly why this has to
    fail loudly.** For a mapping the endpoint drops colliding names and lets the
    registry win; that option does not exist here — the consumer built the
    object and its tool set is theirs. Left alone, pydantic-ai raises
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

    ⚠ **The check exists upstream; what this adds is *when*.**
    ``SpecCapability`` refuses the same thing, but this transport builds its
    capability **per request** — it needs the request-scoped ``seen`` set for
    tool-name dedup — so the upstream refusal would surface as a 500 on the
    first agent call rather than as a failure to start. This wave's whole
    pattern is *fail at registration, not at request time*: an operator reading
    a traceback in ``urls.py`` is in a different situation from one reading it
    in Sentry a week later.

    ⛔ **Why the ambiguity is worth failing over.** ``permission_classes=None``
    means *inherit* over HTTP — the viewset's own classes, then DRF's
    ``DEFAULT_PERMISSION_CLASSES``. Off HTTP neither exists, so a spec that is
    correctly guarded behind a viewset, with passing HTTP tests, becomes
    callable by whatever the model decides to call.

    Imported lazily: ``djangorestframework-services`` arrives with the
    ``[spec-tools]`` extra, and ``service_specs`` being set is what proves it
    is installed.

    ⚠ Constructing a ``DjangoAGUIView`` directly still fails per request — this
    is the documented path, not the only one. The general fix is for the view to
    stop rebuilding the capability every request, which is a larger change to
    the construction seam.
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


__all__ = ["DEFAULT_NAMESPACE", "AGUIServer"]
