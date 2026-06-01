from __future__ import annotations

from abc import ABC, abstractmethod

from asgiref.sync import sync_to_async
from django.http import HttpRequest

from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.utils import owner_id_for


class ModelConversationStore(ABC):
    """Abstract base for a model-backed (or any sync) ``ConversationStore``.

    Provides the async wrapping and per-request owner scoping; a subclass
    implements the three *synchronous* row operations against its own Django
    model (cross-device, auditable persistence). Kept model-agnostic on purpose
    — the package ships no concrete model so it forces no migration; consumers
    define the model, its fields, and the owner relationship.

    Example::

        class MyStore(ModelConversationStore):
            def _fetch(self, thread_id, owner_id):
                row = MyConversation.objects.filter(
                    thread_id=thread_id, owner_id=owner_id,
                ).first()
                return None if row is None else Conversation(...)
            def _store(self, conversation, owner_id): ...
            def _remove(self, thread_id, owner_id): ...
    """

    async def load(self, thread_id: str, *, request: HttpRequest) -> Conversation | None:
        return await sync_to_async(self._fetch)(thread_id, owner_id_for(request))

    async def save(self, conversation: Conversation, *, request: HttpRequest) -> None:
        await sync_to_async(self._store)(conversation, owner_id_for(request))

    async def delete(self, thread_id: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._remove)(thread_id, owner_id_for(request))

    @abstractmethod
    def _fetch(self, thread_id: str, owner_id: str | None) -> Conversation | None: ...

    @abstractmethod
    def _store(self, conversation: Conversation, owner_id: str | None) -> None: ...

    @abstractmethod
    def _remove(self, thread_id: str, owner_id: str | None) -> None: ...


__all__ = ["ModelConversationStore"]
