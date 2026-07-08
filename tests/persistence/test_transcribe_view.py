from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.transcribe_view import TranscribeView


class _FakeBackend:
    """A minimal backend exercising the view without a real STT provider."""

    def __init__(self, *, text: str = "hello world") -> None:
        self.text = text
        self.received: list[Any] = []

    async def transcribe(self, audio: Any, *, request: HttpRequest) -> str:
        self.received.append(audio)
        return self.text


def _audio_request(
    *,
    files: dict[str, Any] | None = None,
    content: bytes = b"audio-bytes",
    content_type: str = "audio/webm",
) -> HttpRequest:
    data = (
        files
        if files is not None
        else {"audio": SimpleUploadedFile("clip.webm", content, content_type=content_type)}
    )
    return RequestFactory().post("/agent/transcribe/", data=data)


def _body(response: Any) -> Any:
    return json.loads(response.content)


async def test_transcribe_returns_text() -> None:
    backend = _FakeBackend(text="the transcript")
    response = await TranscribeView(backend)(_audio_request())
    assert response.status_code == 200
    assert _body(response) == {"text": "the transcript"}
    assert len(backend.received) == 1


async def test_disabled_with_null_backend_is_410() -> None:
    response = await TranscribeView(NullTranscriptionBackend())(_audio_request())
    assert response.status_code == 410
    assert _body(response) == {"error": "transcription is disabled"}


async def test_without_audio_is_400() -> None:
    response = await TranscribeView(_FakeBackend())(_audio_request(files={}))
    assert response.status_code == 400


async def test_with_multiple_clips_is_400() -> None:
    files = {
        "audio": [
            SimpleUploadedFile("a.webm", b"a", content_type="audio/webm"),
            SimpleUploadedFile("b.webm", b"b", content_type="audio/webm"),
        ]
    }
    response = await TranscribeView(_FakeBackend())(_audio_request(files=files))
    assert response.status_code == 400


@override_settings(DJANGO_AG_UI={"TRANSCRIPTION_MAX_BYTES": 1})
async def test_oversize_is_413() -> None:
    response = await TranscribeView(_FakeBackend())(_audio_request(content=b"too big"))
    assert response.status_code == 413
    assert "limit" in _body(response)["error"]


@override_settings(DJANGO_AG_UI={"TRANSCRIPTION_MAX_BYTES": 0})
async def test_max_bytes_zero_disables_the_cap() -> None:
    response = await TranscribeView(_FakeBackend())(_audio_request(content=b"x" * 1000))
    assert response.status_code == 200


async def test_empty_clip_passes_the_size_check() -> None:
    # A zero-byte clip exercises the falsy ``size`` branch in validation.
    response = await TranscribeView(_FakeBackend())(_audio_request(content=b""))
    assert response.status_code == 200


@override_settings(DJANGO_AG_UI={"TRANSCRIPTION_ALLOWED_TYPES": ["audio/webm"]})
async def test_disallowed_type_is_415() -> None:
    response = await TranscribeView(_FakeBackend())(_audio_request(content_type="audio/x-evil"))
    assert response.status_code == 415


@override_settings(DJANGO_AG_UI={"TRANSCRIPTION_ALLOWED_TYPES": ["audio/webm"]})
async def test_allowed_type_passes() -> None:
    response = await TranscribeView(_FakeBackend())(_audio_request(content_type="audio/webm"))
    assert response.status_code == 200


async def test_rejects_non_post() -> None:
    response = await TranscribeView(_FakeBackend())(RequestFactory().get("/agent/transcribe/"))
    assert response.status_code == 405


async def test_anonymous_rejected_when_require_authenticated() -> None:
    view = TranscribeView(_FakeBackend(), require_authenticated=True)
    response = await view(_audio_request())
    assert response.status_code == 401


async def test_get_user_hook_opens_the_endpoint() -> None:
    view = TranscribeView(
        _FakeBackend(),
        require_authenticated=True,
        get_user=lambda _request: SimpleNamespace(is_authenticated=True),
    )
    response = await view(_audio_request())
    assert response.status_code == 200
