from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest


class OpenAITranscriptionBackend:
    """A ready-to-use transcription backend over an OpenAI-compatible API."""

    model: str = "whisper-1"
    """The transcription model name passed to the API."""
    base_url: str | None = None
    """Optional base URL for an OpenAI-compatible endpoint (``None`` → OpenAI)."""
    api_key: str | None = None
    """Key sent to ``base_url`` as the bearer token. ``None`` falls back to the
    SDK's own default, the ``OPENAI_API_KEY`` environment variable — correct for
    OpenAI itself and wrong for anywhere else, so set this whenever ``base_url``
    is set."""
    timeout: float | None = 60.0
    """Per-request timeout (seconds) for the transcription call. The SDK default
    is 10 minutes; a bounded default keeps a stalled upstream from pinning a
    worker. Override on a subclass for slower endpoints."""

    def __init__(self) -> None:
        # The client and its connection pool are cached on the instance.
        self._client: Any = None

    async def transcribe(self, audio: UploadedFile, *, request: HttpRequest) -> str:
        try:
            from openai import AsyncOpenAI
        except ImportError as error:
            raise ImportError(
                "OpenAITranscriptionBackend requires the 'openai' package: "
                "install django-ag-ui[openai]"
            ) from error
        # Off the event loop: a large recording may spool to a temp file, so the read blocks.
        data = await sync_to_async(audio.read)()
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
        result = await self._client.audio.transcriptions.create(
            model=self.model,
            file=(audio.name or "audio", data, audio.content_type or "application/octet-stream"),
        )
        return result.text


__all__ = ["OpenAITranscriptionBackend"]
