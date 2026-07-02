from __future__ import annotations

from typing import Any

from django.urls import URLPattern, path

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.transcribe_view import TranscribeView
from django_ag_ui.persistence.types.attachment_store import AttachmentStore
from django_ag_ui.persistence.types.conversation_store import ConversationStore
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView
from django_ag_ui.utils import AuthorizePredicate, GetUser


def get_urls(
    view: DjangoAGUIView,
    prefix: str = "agent/",
    *,
    skills: SkillRegistry | None = None,
    tools: ToolRegistry | None = None,
    threads: ConversationStore | None = None,
    attachments: AttachmentStore | None = None,
    transcribe: TranscriptionBackend | None = None,
    require_authenticated: bool = False,
    get_user: GetUser | None = None,
    authorize: AuthorizePredicate | None = None,
) -> list[URLPattern]:
    """Return URL patterns mounting ``view`` at ``<prefix>`` (POST, SSE).

    When ``skills`` is given, also mounts a read-only skills catalog at
    ``<prefix>skills/`` (GET, JSON) for the web component's ``data-skills-url``.

    When ``tools`` (the same :class:`ToolRegistry` the view uses) is given, also
    mounts a read-only **tool catalog** at ``<prefix>tools/`` (GET, JSON) for the
    component's ``data-tools-url`` — friendly card labels for server-side tools.

    When ``threads`` (the same :class:`ConversationStore` the view uses, e.g.
    ``resolve_conversation_store(get_settings().conversation_store)``) is given,
    also mounts the **thread index** for the chat-history drawer
    (``data-threads-url``): ``<prefix>threads/`` (GET — owner-scoped metadata)
    and ``<prefix>threads/<id>/`` (GET messages, **PATCH rename**, DELETE).

    When ``attachments`` (an
    :class:`~django_ag_ui.persistence.types.attachment_store.AttachmentStore`,
    e.g. ``resolve_attachment_store(get_settings().attachment_store)``) is given,
    also mounts the **file-upload endpoint** for the composer's
    ``data-attachments-url``: ``<prefix>attachments/`` (POST upload) and
    ``<prefix>attachments/<id>/`` (GET download, DELETE).

    When ``transcribe`` (a
    :class:`~django_ag_ui.persistence.types.transcription_backend.TranscriptionBackend`,
    e.g. ``resolve_transcription_backend(get_settings().transcription_backend)``)
    is given, also mounts the **voice-input endpoint** for the composer's
    ``data-transcribe-url``: ``<prefix>transcribe/`` (POST audio → ``{"text"}``).

    **Authentication seam.** ``require_authenticated`` / ``get_user`` /
    ``authorize`` are forwarded to **every** sub-view this function constructs
    (skills, tools, threads, attachments, transcribe), so one policy locks down
    the whole mount: ``require_authenticated=True`` → ``401`` for anonymous
    requests, an ``authorize`` predicate → ``403`` for an established-but-forbidden
    user (a staff gate that returns JSON, not an HTML login redirect), and
    ``get_user`` establishes the acting user (sync or async). Configure the
    **agent** ``view``'s own auth when you construct :class:`DjangoAGUIView` (it
    takes the same kwargs) — it is passed in pre-built. Everything defaults open
    for backwards compatibility; the endpoints are unauthenticated until you set
    these.

    **Anonymous scoping caveat.** With the endpoints left open (the default) and
    a model-backed store, an anonymous request has no owner id. The reference
    contrib stores refuse anonymous thread / attachment operations unless
    ``ALLOW_ANONYMOUS`` is set (in which case they bucket per browser session) —
    so pass ``require_authenticated=True`` (or a ``get_user`` hook) whenever the
    store persists, rather than relying on owner scoping to isolate anonymous
    visitors from one another.

    Include the result from your project's root URLconf::

        urlpatterns = [
            ...,
            path("", include(get_urls(DjangoAGUIView(registry), tools=registry))),
        ]
    """
    # Splat into every sub-view constructor; typed ``Any`` so the mixed-value
    # dict satisfies each constructor's specific parameter types.
    auth: dict[str, Any] = {
        "require_authenticated": require_authenticated,
        "get_user": get_user,
        "authorize": authorize,
    }
    urls = [path(prefix, view, name="django_ag_ui")]
    if skills is not None:
        urls.append(
            path(f"{prefix}skills/", SkillsView(skills, **auth), name="django_ag_ui_skills")
        )
    if tools is not None:
        urls.append(path(f"{prefix}tools/", ToolsView(tools, **auth), name="django_ag_ui_tools"))
    if threads is not None:
        threads_view = ThreadsView(threads, **auth)
        urls.append(path(f"{prefix}threads/", threads_view, name="django_ag_ui_threads"))
        urls.append(
            path(f"{prefix}threads/<str:thread_id>/", threads_view, name="django_ag_ui_thread")
        )
    if attachments is not None:
        attachments_view = AttachmentsView(attachments, **auth)
        urls.append(
            path(f"{prefix}attachments/", attachments_view, name="django_ag_ui_attachments")
        )
        urls.append(
            path(
                f"{prefix}attachments/<str:attachment_id>/",
                attachments_view,
                name="django_ag_ui_attachment",
            )
        )
    if transcribe is not None:
        urls.append(
            path(
                f"{prefix}transcribe/",
                TranscribeView(transcribe, **auth),
                name="django_ag_ui_transcribe",
            )
        )
    return urls


__all__ = ["get_urls"]
