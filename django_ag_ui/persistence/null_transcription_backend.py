from __future__ import annotations

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


class NullTranscriptionBackend:
    """The default transcription backend: voice input disabled.

    :class:`~django_ag_ui.persistence.transcribe_view.TranscribeView` detects
    this backend and answers ``410 Gone``, so ``transcribe`` is never reached
    through the endpoint and a misconfigured client gets a clear "voice is off"
    signal. Called directly it raises, rather than fabricating a transcript. Pass
    ``AGUIServer(transcription_backend=...)`` to enable voice.
    """

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str:
        raise NotImplementedError(
            "transcription is disabled: pass transcription_backend=YourBackend() "
            "to AGUIServer(...) to enable voice input"
        )


__all__ = ["NullTranscriptionBackend"]
