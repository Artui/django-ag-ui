from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend


async def test_transcribe_raises_loudly() -> None:
    backend = NullTranscriptionBackend()
    audio = SimpleUploadedFile("clip.webm", b"...", content_type="audio/webm")
    request = RequestFactory().post("/agent/transcribe/")
    with pytest.raises(NotImplementedError, match="transcription is disabled"):
        await backend.transcribe(audio, request=request)
