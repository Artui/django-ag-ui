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
