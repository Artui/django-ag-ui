from __future__ import annotations

from uuid import uuid4

from django.core.files.uploadedfile import UploadedFile

from django_ag_ui.contrib.store.models import StoredAttachment
from django_ag_ui.persistence.model_attachment_store import ModelAttachmentStore
from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment


class DefaultAttachmentStore(ModelAttachmentStore):
    """A ready-to-use model-backed store over :class:`StoredAttachment`.

    The batteries-included durable file store: bytes live in Django ``Storage``
    (filesystem by default, S3/GCS via ``STORAGES``), metadata in a row. Enable
    it by adding ``"django_ag_ui.contrib.store"`` to ``INSTALLED_APPS``, running
    ``migrate``, and setting ``DJANGO_AG_UI["ATTACHMENT_STORE"]`` to this class's
    dotted path. For a bespoke schema, subclass :class:`ModelAttachmentStore`.

    Owner scoping: ``owner_id`` is stored as ``""`` for anonymous requests (the
    ``ModelAttachmentStore`` base passes ``None``), so the unique
    ``(owner_id, attachment_id)`` constraint holds and every query filters by
    owner — one user's id never resolves another's file. The public
    ``attachment_id`` is an opaque UUID, kept separate from the storage filename.
    """

    def _save(self, upload: UploadedFile, owner_id: str | None) -> AttachmentRef:
        attachment_id = uuid4().hex
        name = _basename(upload.name)
        mime = upload.content_type or ""
        size = upload.size or 0
        row = StoredAttachment(
            attachment_id=attachment_id,
            owner_id=owner_id or "",
            name=name,
            mime=mime,
            size=size,
        )
        # ``save=False`` writes the bytes through Storage but defers the row
        # INSERT to the single ``row.save()`` below.
        row.file.save(attachment_id, upload, save=False)
        row.save()
        return AttachmentRef(id=attachment_id, name=name, mime=mime, size=size)

    def _open(self, attachment_id: str, owner_id: str | None) -> OpenedAttachment | None:
        row = StoredAttachment.objects.filter(
            owner_id=owner_id or "", attachment_id=attachment_id
        ).first()
        if row is None:
            return None
        with row.file.open("rb") as handle:
            content = handle.read()
        return OpenedAttachment(
            ref=AttachmentRef(id=row.attachment_id, name=row.name, mime=row.mime, size=row.size),
            content=content,
        )

    def _remove(self, attachment_id: str, owner_id: str | None) -> None:
        row = StoredAttachment.objects.filter(
            owner_id=owner_id or "", attachment_id=attachment_id
        ).first()
        if row is None:
            return
        # Delete the bytes through Storage, then the row.
        row.file.delete(save=False)
        row.delete()


def _basename(name: str | None) -> str:
    """The trailing filename component, stripped of any path; never empty."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base or "attachment"


__all__ = ["DefaultAttachmentStore"]
