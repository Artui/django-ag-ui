from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.transcribe_view import TranscribeView
from tests.authed_request_factory import AuthedRequestFactory


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
    anonymous: bool = False,
) -> HttpRequest:
    data = (
        files
        if files is not None
        else {"audio": SimpleUploadedFile("clip.webm", content, content_type=content_type)}
    )
    factory = RequestFactory() if anonymous else AuthedRequestFactory()
    return factory.post("/agent/transcribe/", data=data)


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
    response = await TranscribeView(_FakeBackend())(
        AuthedRequestFactory().get("/agent/transcribe/")
    )
    assert response.status_code == 405


async def test_anonymous_rejected_by_default() -> None:
    response = await TranscribeView(_FakeBackend())(_audio_request(anonymous=True))
    assert response.status_code == 401


async def test_anonymous_accepted_when_authentication_is_waived() -> None:
    view = TranscribeView(_FakeBackend(), require_authenticated=False)
    response = await view(_audio_request(anonymous=True))
    assert response.status_code == 200


async def test_get_user_hook_opens_the_endpoint() -> None:
    view = TranscribeView(
        _FakeBackend(),
        get_user=lambda _request: SimpleNamespace(is_authenticated=True),
    )
    response = await view(_audio_request(anonymous=True))
    assert response.status_code == 200


@override_settings(DJANGO_AG_UI={"TRANSCRIPTION_MAX_BYTES": 4})
async def test_oversize_is_aborted_mid_parse_rather_than_spooled() -> None:
    """413 is the same answer either way; what changed is what it costs to give.

    Checking ``audio.size`` after ``request.FILES`` means Django has already
    written the whole part out — to ``FILE_UPLOAD_TEMP_DIR`` once it outgrows
    memory — so the cap bounded what reached the backend, not what reached the
    disk. No Django-level ceiling covers that either:
    ``DATA_UPLOAD_MAX_MEMORY_SIZE`` excludes file uploads and
    ``FILE_UPLOAD_MAX_MEMORY_SIZE`` only chooses memory over a temp file.
    """
    request = _audio_request(content=b"x" * 4096)
    response = await TranscribeView(_FakeBackend())(request)

    assert response.status_code == 413
    # Nothing was assembled: the guard stopped the parser while the body was
    # still arriving, so there is no file to have spooled.
    assert list(request.FILES.keys()) == []


@override_settings(DJANGO_AG_UI={"TRANSCRIPTION_MAX_BYTES": 4})
async def test_an_oversize_clip_never_reaches_the_backend() -> None:
    backend = _FakeBackend()
    await TranscribeView(backend)(_audio_request(content=b"x" * 4096))

    assert backend.received == []


class _Throttle:
    """A limiter that denies after ``allow`` calls, recording each one."""

    def __init__(self, *, allow: int = 0, retry_after: int = 30) -> None:
        self.allow = allow
        self.retry_after = retry_after
        self.calls: list[Any] = []

    def consume(self, request: HttpRequest) -> int | None:
        self.calls.append(request)
        if len(self.calls) <= self.allow:
            return None
        return self.retry_after


async def test_a_throttle_can_limit_the_endpoint() -> None:
    """Authentication bounds who may spend, not how often.

    The backend is a paid provider call per request, so an authenticated caller
    looping small valid clips is a bill. This is the only seam that stops it
    without middleware.
    """
    throttle = _Throttle(allow=1)
    view = TranscribeView(_FakeBackend(), throttle=throttle)

    assert (await view(_audio_request())).status_code == 200
    denied = await view(_audio_request())

    assert denied.status_code == 429
    assert denied["Retry-After"] == "30"
    assert _body(denied) == {"error": "rate limited", "retry_after": 30}


async def test_a_throttled_request_never_reaches_the_backend() -> None:
    backend = _FakeBackend()
    await TranscribeView(backend, throttle=_Throttle())(_audio_request())

    assert backend.received == []


async def test_the_throttle_runs_after_authentication() -> None:
    """So a limiter may key on the acting user rather than only on an IP."""
    throttle = _Throttle()
    view = TranscribeView(_FakeBackend(), throttle=throttle)

    response = await view(_audio_request(anonymous=True))

    assert response.status_code == 401
    assert throttle.calls == []


async def test_the_throttle_runs_before_the_body_is_parsed() -> None:
    """A rejected request must not pay for the upload it is being refused."""
    request = _audio_request(content=b"x" * 512)
    await TranscribeView(_FakeBackend(), throttle=_Throttle())(request)

    # Django caches the parsed multipart body on the request the first time it is
    # read, so the absence of that cache is the fact worth asserting: reading
    # ``request.FILES`` here would parse it and prove nothing.
    assert "_files" not in request.__dict__


async def test_an_async_consume_is_refused_at_construction() -> None:
    """A coroutine is neither None nor an int, so it would read as rate limited.

    Caught where it can be read as a misconfiguration rather than at request
    time, where every clip would come back 429 with a coroutine for a
    ``Retry-After``.
    """

    class AsyncThrottle:
        async def consume(self, request: HttpRequest) -> int | None:
            return None

    with pytest.raises(ImproperlyConfigured, match="declared 'async def'"):
        TranscribeView(_FakeBackend(), throttle=AsyncThrottle())
