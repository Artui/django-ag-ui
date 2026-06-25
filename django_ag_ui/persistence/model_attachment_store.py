from __future__ import annotations

from abc import ABC, abstractmethod

from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment
from django_ag_ui.persistence.utils import owner_id_for


class ModelAttachmentStore(ABC):
    """Abstract base for a model-backed (or any sync) ``AttachmentStore``.

    Provides the async wrapping and per-request owner scoping — the same shape as
    :class:`~django_ag_ui.ModelConversationStore` — so a subclass implements
    three *synchronous* operations against its own storage (a Django ``Storage``
    for the bytes, a model row for the metadata). Kept model-agnostic on purpose:
    the package ships no concrete model so it forces no migration; the opt-in
    ``django_ag_ui.contrib.store`` app supplies one.

    Each ``_save`` / ``_open`` / ``_remove`` receives the resolved ``owner_id``
    (``None`` for anonymous) and must filter by it so files never cross users.

    Example::

        class MyStore(ModelAttachmentStore):
            def _save(self, upload, owner_id):
                row = MyAttachment.objects.create(owner_id=owner_id or "", ...)
                row.file.save(row.attachment_id, upload, save=True)
                return AttachmentRef(id=row.attachment_id, name=..., mime=..., size=...)
            def _open(self, attachment_id, owner_id): ...
            def _remove(self, attachment_id, owner_id): ...
    """

    async def save(self, upload: UploadedFile, *, request: HttpRequest) -> AttachmentRef:
        return await sync_to_async(self._save)(upload, owner_id_for(request))

    async def open(self, attachment_id: str, *, request: HttpRequest) -> OpenedAttachment | None:
        return await sync_to_async(self._open)(attachment_id, owner_id_for(request))

    async def delete(self, attachment_id: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._remove)(attachment_id, owner_id_for(request))

    @abstractmethod
    def _save(self, upload: UploadedFile, owner_id: str | None) -> AttachmentRef: ...

    @abstractmethod
    def _open(self, attachment_id: str, owner_id: str | None) -> OpenedAttachment | None: ...

    @abstractmethod
    def _remove(self, attachment_id: str, owner_id: str | None) -> None: ...


__all__ = ["ModelAttachmentStore"]
