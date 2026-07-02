from __future__ import annotations

from dataclasses import dataclass
from typing import IO

from django_ag_ui.persistence.types.attachment_ref import AttachmentRef


@dataclass(frozen=True)
class OpenedAttachment:
    """An attachment's metadata paired with a readable byte stream.

    Returned by :meth:`AttachmentStore.open
    <django_ag_ui.persistence.types.attachment_store.AttachmentStore>` so the
    download view and the ``read_attachment`` tool both get the content *and* the
    :class:`AttachmentRef` (name / mime / size) in a single owner-scoped call.

    ``content`` is an **open, readable binary stream** (a file handle), not the
    whole bytes — so a large attachment streams out via ``FileResponse`` rather
    than being buffered in memory (AGH-3). The consumer owns it: the download view
    hands it to ``FileResponse`` (which closes it) and the tool reads it under a
    ``with`` block. Read it exactly once.
    """

    ref: AttachmentRef
    content: IO[bytes]


__all__ = ["OpenedAttachment"]
