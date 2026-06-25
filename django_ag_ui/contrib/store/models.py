from __future__ import annotations

from django.db import models


class StoredConversation(models.Model):
    """The reference durable conversation row for a model-backed store.

    One row per ``(owner_id, thread_id)``. ``messages`` holds the AG-UI message
    list as JSON (the same shape every store round-trips); ``title`` and
    ``preview`` are denormalised so the thread drawer's list query never loads
    message bodies, and ``updated_at`` orders it. ``owner_id`` is the acting
    user's id (``""`` for anonymous) — the store always filters by it.

    Used by
    :class:`~django_ag_ui.contrib.store.default_conversation_store.DefaultConversationStore`.
    Run lineage (``run_id`` / step ledger) is intentionally out of scope here;
    a future durability layer can extend the schema.
    """

    thread_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    preview = models.TextField(blank=True, default="")
    messages = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id", "thread_id"], name="django_ag_ui_store_owner_thread_unique"
            )
        ]
        indexes = [models.Index(fields=["owner_id", "-updated_at"])]

    def __str__(self) -> str:
        return self.title or self.thread_id


class StoredAttachment(models.Model):
    """The reference durable attachment row for a model-backed store.

    The opaque ``attachment_id`` is the handle the wire ref carries; ``file``
    holds the bytes via Django ``Storage`` (so S3 etc. come free through
    ``STORAGES``/``DEFAULT_FILE_STORAGE``), while ``name`` / ``mime`` / ``size``
    are the denormalised metadata returned without reading the file back.
    ``owner_id`` is the acting user's id (``""`` for anonymous) — the store
    always filters by it, the security boundary. ``thread_id`` optionally ties an
    attachment to one conversation; it is left blank when a file is uploaded
    before a thread exists, so attachments never depend on a conversation row.

    Used by
    :class:`~django_ag_ui.contrib.store.default_attachment_store.DefaultAttachmentStore`.
    """

    attachment_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    thread_id = models.CharField(max_length=255, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    mime = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveBigIntegerField(default=0)
    file = models.FileField(upload_to="django_ag_ui/attachments/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id", "attachment_id"],
                name="django_ag_ui_attachment_owner_id_unique",
            )
        ]
        indexes = [models.Index(fields=["owner_id", "-created_at"])]

    def __str__(self) -> str:
        return self.name or self.attachment_id
