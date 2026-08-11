from __future__ import annotations

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


class NullTranscriptionBackend:
    """The default transcription backend: voice input disabled.

    ``transcribe`` is never reached through the endpoint — the
    :class:`~django_ag_ui.persistence.transcribe_view.TranscribeView` detects
    this backend and returns ``410 Gone`` so a misconfigured client gets a clear
    "voice is off" signal instead of a silent failure. ``transcribe`` still
    raises if called directly, to fail loudly rather than fabricate a transcript.
    The endpoint is inert until a real backend is passed as
    ``AGUIServer(transcription_backend=...)``.
    """

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str:
        raise NotImplementedError(
            "transcription is disabled: pass transcription_backend=YourBackend() "
            "to AGUIServer(...) to enable voice input"
        )


__all__ = ["NullTranscriptionBackend"]
