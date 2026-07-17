from __future__ import annotations

from abc import ABC, abstractmethod

from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from django_ag_ui.conf import get_setting
from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment
from django_ag_ui.persistence.utils import resolve_owner_id


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

    # Class-level default (an immutable bool, so no shared-mutable hazard) so a
    # subclass that overrides __init__ and forgets super() fails **closed** —
    # refusing anonymous requests — rather than raising AttributeError at
    # request time or, worse, defaulting open.
    _allow_anonymous: bool = False

    def __init__(self, *, allow_anonymous: bool | None = None) -> None:
        """``allow_anonymous`` governs whether anonymous requests are served.

        ``False`` (the default) refuses them rather than collapsing every
        anonymous visitor into one shared owner bucket, where they could read and
        delete each other's data. ``None`` takes
        ``DJANGO_AG_UI["ALLOW_ANONYMOUS"]``, resolved once here — it is a *store*
        policy, so two endpoints sharing a store necessarily agree on it.

        A subclass that overrides ``__init__`` must call ``super().__init__()``.
        """
        self._allow_anonymous: bool = (
            allow_anonymous
            if allow_anonymous is not None
            else bool(get_setting("ALLOW_ANONYMOUS", False))
        )

    async def save(self, upload: UploadedFile, *, request: HttpRequest) -> AttachmentRef:
        return await sync_to_async(self._save_scoped)(upload, request)

    async def open(self, attachment_id: str, *, request: HttpRequest) -> OpenedAttachment | None:
        return await sync_to_async(self._open_scoped)(attachment_id, request)

    async def delete(self, attachment_id: str, *, request: HttpRequest) -> None:
        await sync_to_async(self._remove_scoped)(attachment_id, request)

    # Owner resolution + the sync op run in one thread (``resolve_owner_id`` may
    # create a session row for the anonymous bucket, so it can't run on the event
    # loop). ``AnonymousOperationError`` propagates up to the view (→ 403).
    def _save_scoped(self, upload: UploadedFile, request: HttpRequest) -> AttachmentRef:
        return self._save(upload, resolve_owner_id(request, allow_anonymous=self._allow_anonymous))

    def _open_scoped(self, attachment_id: str, request: HttpRequest) -> OpenedAttachment | None:
        return self._open(
            attachment_id, resolve_owner_id(request, allow_anonymous=self._allow_anonymous)
        )

    def _remove_scoped(self, attachment_id: str, request: HttpRequest) -> None:
        self._remove(
            attachment_id, resolve_owner_id(request, allow_anonymous=self._allow_anonymous)
        )

    @abstractmethod
    def _save(self, upload: UploadedFile, owner_id: str | None) -> AttachmentRef: ...

    @abstractmethod
    def _open(self, attachment_id: str, owner_id: str | None) -> OpenedAttachment | None: ...

    @abstractmethod
    def _remove(self, attachment_id: str, owner_id: str | None) -> None: ...


__all__ = ["ModelAttachmentStore"]
