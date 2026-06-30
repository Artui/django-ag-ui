from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from django_ag_ui.contrib.transcription.openai_transcription_backend import (
    OpenAITranscriptionBackend,
)


class _FakeTranscriptions:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    async def create(self, *, model: str, file: Any) -> SimpleNamespace:
        self._calls.append({"model": model, "file": file})
        return SimpleNamespace(text="the transcript")


class _FakeClient:
    """Stand-in for ``openai.AsyncOpenAI``; records construction + call args."""

    calls: list[dict[str, Any]] = []
    init_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeClient.init_kwargs = kwargs
        self.audio = SimpleNamespace(transcriptions=_FakeTranscriptions(_FakeClient.calls))


def _audio() -> SimpleUploadedFile:
    return SimpleUploadedFile("clip.webm", b"audio-bytes", content_type="audio/webm")


def _request() -> Any:
    return RequestFactory().post("/agent/transcribe/")


async def test_transcribes_via_the_openai_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.calls = []
    monkeypatch.setattr("openai.AsyncOpenAI", _FakeClient)

    text = await OpenAITranscriptionBackend().transcribe(_audio(), request=_request())

    assert text == "the transcript"
    # Default model + no base_url; the audio rides as a (name, bytes, mime) tuple.
    assert _FakeClient.init_kwargs == {"base_url": None}
    (call,) = _FakeClient.calls
    assert call["model"] == "whisper-1"
    assert call["file"] == ("clip.webm", b"audio-bytes", "audio/webm")


async def test_subclass_overrides_model_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.calls = []
    monkeypatch.setattr("openai.AsyncOpenAI", _FakeClient)

    class GroqTranscription(OpenAITranscriptionBackend):
        model = "whisper-large-v3"
        base_url = "https://api.groq.com/openai/v1"

    await GroqTranscription().transcribe(_audio(), request=_request())

    assert _FakeClient.init_kwargs == {"base_url": "https://api.groq.com/openai/v1"}
    assert _FakeClient.calls[0]["model"] == "whisper-large-v3"


async def test_missing_openai_dependency_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate the extra not being installed: a ``None`` entry makes the import
    # raise ImportError, which the backend rewraps with an install hint.
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ImportError, match=r"django-ag-ui\[openai\]"):
        await OpenAITranscriptionBackend().transcribe(_audio(), request=_request())
