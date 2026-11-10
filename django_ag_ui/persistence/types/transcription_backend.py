from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


@runtime_checkable
class TranscriptionBackend(Protocol):
    """Pluggable server-side speech-to-text for the composer's voice input."""

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str: ...


__all__ = ["TranscriptionBackend"]
