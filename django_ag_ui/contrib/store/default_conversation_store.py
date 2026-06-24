from __future__ import annotations

from django.utils import timezone

from django_ag_ui.contrib.store.models import StoredConversation
from django_ag_ui.persistence.model_conversation_store import ModelConversationStore
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMeta, ConversationMetaList
from django_ag_ui.persistence.utils import (
    derive_preview,
    derive_title,
    messages_from_jsonable,
    messages_to_jsonable,
)


class DefaultConversationStore(ModelConversationStore):
    """A ready-to-use model-backed store over :class:`StoredConversation`.

    The batteries-included durable store: cross-device, per-user history with a
    cheap thread list. Enable it by adding ``"django_ag_ui.contrib.store"`` to
    ``INSTALLED_APPS``, running ``migrate``, and setting
    ``DJANGO_AG_UI["CONVERSATION_STORE"]`` to this class's dotted path. For a
    bespoke schema, subclass :class:`ModelConversationStore` instead.

    Owner scoping: ``owner_id`` is stored as ``""`` for anonymous requests (the
    ``ModelConversationStore`` base passes ``None``), so the unique
    ``(owner_id, thread_id)`` constraint holds and every query filters by owner.
    Titles are derived from the first user message at first save and then left
    alone except by :meth:`_rename`; ``preview`` re-derives on every save.
    """

    def _fetch(self, thread_id: str, owner_id: str | None) -> Conversation | None:
        row = StoredConversation.objects.filter(
            owner_id=owner_id or "", thread_id=thread_id
        ).first()
        if row is None:
            return None
        return Conversation(
            thread_id=row.thread_id,
            messages=messages_from_jsonable(row.messages),
            owner_id=row.owner_id or None,
        )

    def _store(self, conversation: Conversation, owner_id: str | None) -> None:
        messages = conversation.messages
        defaults = {
            "messages": messages_to_jsonable(messages),
            "preview": derive_preview(messages),
        }
        row, created = StoredConversation.objects.get_or_create(
            owner_id=owner_id or "",
            thread_id=conversation.thread_id,
            defaults={**defaults, "title": derive_title(messages)},
        )
        if not created:
            # Preserve the title (derived-at-creation or renamed); refresh the
            # body + preview to the latest exchange. ``.update()`` bypasses the
            # ``auto_now`` field, so bump ``updated_at`` explicitly to keep the
            # drawer ordered by recency.
            StoredConversation.objects.filter(pk=row.pk).update(
                **defaults, updated_at=timezone.now()
            )

    def _remove(self, thread_id: str, owner_id: str | None) -> None:
        StoredConversation.objects.filter(owner_id=owner_id or "", thread_id=thread_id).delete()

    def _list(self, owner_id: str | None) -> ConversationMetaList:
        rows = (
            StoredConversation.objects.filter(owner_id=owner_id or "")
            .order_by("-updated_at")
            .values("thread_id", "title", "preview", "updated_at", "owner_id")
        )
        return [
            ConversationMeta(
                thread_id=row["thread_id"],
                title=row["title"],
                updated_at=row["updated_at"],
                preview=row["preview"],
                owner_id=row["owner_id"] or None,
            )
            for row in rows
        ]

    def _rename(self, thread_id: str, title: str, owner_id: str | None) -> None:
        StoredConversation.objects.filter(owner_id=owner_id or "", thread_id=thread_id).update(
            title=title
        )


__all__ = ["DefaultConversationStore"]
