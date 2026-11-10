from __future__ import annotations

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


class NullTranscriptionBackend:
    """The default transcription backend: voice input disabled."""

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str:
        raise NotImplementedError(
            "transcription is disabled: pass transcription_backend=YourBackend() "
            "to AGUIServer(...) to enable voice input"
        )


__all__ = ["NullTranscriptionBackend"]
