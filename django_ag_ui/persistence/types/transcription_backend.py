from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


@runtime_checkable
class TranscriptionBackend(Protocol):
    """Pluggable server-side speech-to-text for the composer's voice input.

    Resolved from ``DJANGO_AG_UI["TRANSCRIPTION_BACKEND"]``. The package ships a
    no-op default (:class:`~django_ag_ui.NullTranscriptionBackend` — voice off)
    and an opt-in reference implementation over an OpenAI-compatible
    ``/audio/transcriptions`` endpoint
    (:class:`~django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend`).

    The single method is async and receives the acting ``request`` so a backend
    can scope by user / rate-limit / bill per principal. Unlike
    :class:`~django_pydantic_agent.persistence.types.attachment_store.AttachmentStore`,
    transcription keeps no durable artifact: the recorded audio is transcribed
    and the text returned in one shot (the composer drops it into the textarea),
    so there is nothing to ``open`` or ``delete``. ``transcribe`` validates
    nothing about size/type itself (the view does, from settings); it just turns
    audio bytes into text.
    """

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str: ...


__all__ = ["TranscriptionBackend"]
