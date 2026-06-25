from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentRef:
    """A durable, lightweight reference to one uploaded file.

    What an upload returns and what travels on the wire — never the bytes.
    A user drops a file into the composer, it is uploaded out-of-band to the
    :class:`~django_ag_ui.persistence.attachments_view.AttachmentsView`, and the
    server hands back this ref; the client holds it on the message and the agent
    reads the actual bytes server-side via the ``read_attachment`` tool. Keeping
    the AG-UI message stream free of base64 mirrors how the tool catalog keeps
    schemas off the wire.

    ``id`` is the opaque, owner-scoped handle the store resolves back to bytes;
    ``mime`` is the declared content type (client-supplied, so treat it as a
    hint); ``size`` is the byte count; ``url`` is an optional direct fetch URL
    (e.g. the owner-checked download endpoint) and stays ``None`` unless a store
    fills it in.
    """

    id: str
    name: str
    mime: str
    size: int
    url: str | None = None


__all__ = ["AttachmentRef"]
