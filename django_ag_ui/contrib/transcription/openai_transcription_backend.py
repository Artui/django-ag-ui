from __future__ import annotations

from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


class OpenAITranscriptionBackend:
    """A ready-to-use transcription backend over an OpenAI-compatible API.

    The batteries-included voice backend: it forwards the recorded clip to an
    OpenAI ``/audio/transcriptions`` endpoint and returns the text. Enable it by
    installing the ``[openai]`` extra and pointing
    ``DJANGO_AG_UI["TRANSCRIPTION_BACKEND"]`` at this class's dotted path
    (``django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend``).

    Self-configuring so :func:`~django_ag_ui.resolve_transcription_backend` can
    instantiate it with no arguments: the API key comes from the ``OPENAI_API_KEY``
    environment variable (the SDK default). Override the model or point at an
    OpenAI-compatible server (Azure OpenAI, a local Whisper server, Groq, …) by
    subclassing and setting the class attributes::

        class GroqTranscription(OpenAITranscriptionBackend):
            model = "whisper-large-v3"
            base_url = "https://api.groq.com/openai/v1"

    The ``openai`` SDK is imported lazily inside :meth:`transcribe` so the base
    package keeps it an optional dependency (the ``[openai]`` extra).
    """

    #: The transcription model name passed to the API.
    model: str = "whisper-1"
    #: Optional base URL for an OpenAI-compatible endpoint (``None`` → OpenAI).
    base_url: str | None = None

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str:
        try:
            from openai import AsyncOpenAI
        except ImportError as error:
            raise ImportError(
                "OpenAITranscriptionBackend requires the 'openai' package: "
                "install django-ag-ui[openai]"
            ) from error
        # Read the bytes off the event loop — a large recording may be spooled to
        # a temp file, so the read is blocking I/O.
        data = await sync_to_async(audio.read)()
        client = AsyncOpenAI(base_url=self.base_url)
        result = await client.audio.transcriptions.create(
            model=self.model,
            file=(audio.name or "audio", data, audio.content_type or "application/octet-stream"),
        )
        return result.text


__all__ = ["OpenAITranscriptionBackend"]
