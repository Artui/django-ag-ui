from __future__ import annotations

from typing import Any

from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class CappedUploadHandler(FileUploadHandler):
    """A monitoring upload handler that aborts once the upload exceeds a cap.

    Inserted **first** in ``request.upload_handlers`` so it counts bytes as they
    stream in and raises :class:`~django.core.files.uploadhandler.StopUpload` the
    moment the file passes ``max_bytes`` — before Django spools the whole
    (oversized) body to a temp file. It never produces the file itself: chunks
    pass through to the downstream handler (which builds the file for an in-cap
    upload), and :meth:`file_complete` returns ``None``. After parsing, the view
    reads :attr:`exceeded` to answer ``413`` rather than a bare "no file".

    ``max_bytes`` of ``0`` disables the cap (the handler is a no-op passthrough).
    """

    def __init__(self, max_bytes: int, request: Any = None) -> None:
        super().__init__(request)
        self.max_bytes = max_bytes
        self.exceeded = False
        self._received = 0

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes | None:
        self._received += len(raw_data)
        if self.max_bytes and self._received > self.max_bytes:
            self.exceeded = True
            # ``connection_reset`` stops the parser reading the rest of the body.
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size: int) -> None:
        return None


__all__ = ["CappedUploadHandler"]
