from __future__ import annotations

from dataclasses import dataclass

from django_ag_ui.persistence.types.attachment_ref import AttachmentRef


@dataclass(frozen=True)
class OpenedAttachment:
    """An attachment's metadata paired with its bytes — the result of reading one.

    Returned by :meth:`AttachmentStore.open
    <django_ag_ui.persistence.types.attachment_store.AttachmentStore>` so the
    download view and the ``read_attachment`` tool both get the content *and* the
    :class:`AttachmentRef` (name / mime / size) in a single owner-scoped call.
    The bytes are read whole — uploads are size-capped — so callers don't manage
    a stream.
    """

    ref: AttachmentRef
    content: bytes


__all__ = ["OpenedAttachment"]
