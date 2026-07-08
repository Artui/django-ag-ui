from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment


@runtime_checkable
class AttachmentStore(Protocol):
    """Pluggable server-side storage for files a user attaches to a conversation.

    Resolved from ``DJANGO_AG_UI["ATTACHMENT_STORE"]``. The package ships a
    no-op default (:class:`~django_ag_ui.NullAttachmentStore` — uploads off) and
    an abstract model-backed base
    (:class:`~django_ag_ui.ModelAttachmentStore`); the opt-in
    ``django_ag_ui.contrib.store`` app provides a ready
    :class:`~django_ag_ui.contrib.store.default_attachment_store.DefaultAttachmentStore`
    that keeps bytes in Django ``Storage`` (so S3 etc. come free via
    ``STORAGES``/``DEFAULT_FILE_STORAGE``) and metadata in a row.

    Every method is async and **owner-scoped**: a store filters by the acting
    user so one user can never read or delete another's files — the security
    boundary for the whole feature. ``save`` validates nothing about size/type
    itself (the view does, from settings); it just persists the bytes and
    returns a durable :class:`AttachmentRef`. ``open`` returns ``None`` for a
    missing or cross-owner id rather than raising, so callers map it to a 404.
    """

    async def save(self, upload: UploadedFile, *, request: HttpRequest) -> AttachmentRef: ...
    async def open(
        self, attachment_id: str, *, request: HttpRequest
    ) -> OpenedAttachment | None: ...
    async def delete(self, attachment_id: str, *, request: HttpRequest) -> None: ...


__all__ = ["AttachmentStore"]
