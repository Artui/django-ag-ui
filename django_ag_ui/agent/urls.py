from __future__ import annotations

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


def get_urls(
    view: DjangoAGUIView,
    prefix: str = "agent/",
    *,
    skills: SkillRegistry | None = None,
    tools: ToolRegistry | None = None,
    threads: ConversationStore | None = None,
    attachments: AttachmentStore | None = None,
    transcribe: TranscriptionBackend | None = None,
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
    and ``<prefix>threads/<id>/`` (GET messages, DELETE). Both default open like
    the catalogs — mount :class:`~django_ag_ui.persistence.threads_view.ThreadsView`
    yourself with ``require_authenticated`` / ``get_user`` to lock them down.

    When ``attachments`` (an
    :class:`~django_ag_ui.persistence.types.attachment_store.AttachmentStore`,
    e.g. ``resolve_attachment_store(get_settings().attachment_store)``) is given,
    also mounts the **file-upload endpoint** for the composer's
    ``data-attachments-url``: ``<prefix>attachments/`` (POST upload) and
    ``<prefix>attachments/<id>/`` (GET download, DELETE). Owner-scoped and open
    by default like the catalogs — mount
    :class:`~django_ag_ui.persistence.attachments_view.AttachmentsView` yourself
    to lock it down.

    When ``transcribe`` (a
    :class:`~django_ag_ui.persistence.types.transcription_backend.TranscriptionBackend`,
    e.g. ``resolve_transcription_backend(get_settings().transcription_backend)``)
    is given, also mounts the **voice-input endpoint** for the composer's
    ``data-transcribe-url``: ``<prefix>transcribe/`` (POST audio → ``{"text"}``).
    Open by default like the catalogs — mount
    :class:`~django_ag_ui.persistence.transcribe_view.TranscribeView` yourself to
    lock it down.

    Include the result from your project's root URLconf::

        urlpatterns = [
            ...,
            path("", include(get_urls(DjangoAGUIView(registry), tools=registry))),
        ]
    """
    urls = [path(prefix, view, name="django_ag_ui")]
    if skills is not None:
        urls.append(path(f"{prefix}skills/", SkillsView(skills), name="django_ag_ui_skills"))
    if tools is not None:
        urls.append(path(f"{prefix}tools/", ToolsView(tools), name="django_ag_ui_tools"))
    if threads is not None:
        threads_view = ThreadsView(threads)
        urls.append(path(f"{prefix}threads/", threads_view, name="django_ag_ui_threads"))
        urls.append(
            path(f"{prefix}threads/<str:thread_id>/", threads_view, name="django_ag_ui_thread")
        )
    if attachments is not None:
        attachments_view = AttachmentsView(attachments)
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
            path(f"{prefix}transcribe/", TranscribeView(transcribe), name="django_ag_ui_transcribe")
        )
    return urls


__all__ = ["get_urls"]
