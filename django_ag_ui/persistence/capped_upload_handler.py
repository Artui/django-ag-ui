from __future__ import annotations

from typing import Any

from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class CappedUploadHandler(FileUploadHandler):
    """A monitoring upload handler that aborts once the upload exceeds a cap."""

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
