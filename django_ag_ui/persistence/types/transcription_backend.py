from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


@runtime_checkable
class TranscriptionBackend(Protocol):
    """Pluggable server-side speech-to-text for the composer's voice input.

    Passed as ``AGUIServer(transcription_backend=...)``. The package ships a
    no-op default (:class:`~django_ag_ui.NullTranscriptionBackend` — voice off)
    and an opt-in reference implementation over an OpenAI-compatible
    ``/audio/transcriptions`` endpoint
    (:class:`~django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend`).

    The single method is async and receives the acting ``request`` so a backend
    can scope by user, rate-limit, or bill per principal. Unlike an
    :class:`~django_pydantic_agent.persistence.types.attachment_store.AttachmentStore`,
    transcription keeps no durable artifact — audio in, text out, nothing to
    ``open`` or ``delete``. ``transcribe`` validates nothing about size or type;
    the view does that from settings.
    """

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str: ...


__all__ = ["TranscriptionBackend"]
