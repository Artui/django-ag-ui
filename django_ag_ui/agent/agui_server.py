from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest
from django.urls import URLPattern, path
from django_pydantic_agent.agent.types.agent_factory_fn import AgentFactoryFn
from django_pydantic_agent.persistence.null_attachment_store import NullAttachmentStore
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.persistence.types.attachment_store import AttachmentStore
from django_pydantic_agent.persistence.types.conversation_store import ConversationStore
from django_pydantic_agent.policy.audit.types.audit_logger import AuditLogger
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from django_pydantic_agent.utils import AuthorizePredicate, GetUser

from django_ag_ui.agent.agui_view import DjangoAGUIView
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
    - ``resume`` / ``fork`` — when a ``step_store`` is configured
      (``resume/<run_id>/`` + ``fork/<run_id>/``): seed a new run from a prior
      run's last continuable snapshot.

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

    **Durable step persistence.** ``step_store`` is a *factory* — a
    ``request -> StepStore`` callable, not a shared store instance, because the
    ``pydantic-ai-harness`` step-store protocol carries no request, so the store
    binds one and is built per run. When set, every run attaches a
    ``StepPersistence`` capability that records an owner-scoped run / event /
    snapshot / tool-effect ledger through that store. Pass
    :class:`~django_pydantic_agent.contrib.store.default_step_store.DefaultStepStore` (its
    constructor *is* the ``request -> StepStore`` factory) for the reference
    model-backed store, or any such callable. Requires the
    ``django-ag-ui[harness]`` extra. Configuring it also mounts the owner-scoped
    ``resume/<run_id>/`` and ``fork/<run_id>/`` endpoints, which seed a new run
    with a prior run's last continuable snapshot.

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
        attachment_store: AttachmentStore | None = None,
        transcription_backend: TranscriptionBackend | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
        agent_factory: AgentFactoryFn | None = None,
        drf_mcp_server: Any = None,
        service_specs: dict[str, Any] | None = None,
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
        self._service_specs = service_specs
        # A per-request factory, not a shared store (the harness step-store
        # protocol carries no request). Retained on the object because the
        # resume/fork endpoints a later release mounts will need it too.
        self._step_store = step_store
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
            service_specs=service_specs,
            provider=provider,
            attachment_store=self._attachment_store,
            conversation_store=self._conversation_store,
            step_store=self._step_store,
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


__all__ = ["DEFAULT_NAMESPACE", "AGUIServer"]
