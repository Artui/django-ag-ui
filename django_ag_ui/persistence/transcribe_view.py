from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction, sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import (
    HttpRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.response import HttpResponseBase

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend
from django_ag_ui.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response


class TranscribeView:
    """Owner-scoped speech-to-text endpoint (async, multipart in / JSON out).

    Mounted by :func:`~django_ag_ui.get_urls` with ``transcribe=<backend>`` over
    a :class:`~django_ag_ui.persistence.types.transcription_backend.TranscriptionBackend`:

    - ``POST <prefix>transcribe/`` → multipart audio under the ``audio`` field;
      validates size/type from ``DJANGO_AG_UI`` settings, runs the backend, and
      returns ``200`` with ``{"text": "<transcript>"}``.

    The audio is transcribed and discarded — nothing is stored — so unlike
    :class:`~django_ag_ui.persistence.attachments_view.AttachmentsView` there is
    no download/delete route. The view carries the same authentication seam as
    :class:`~django_ag_ui.DjangoAGUIView` (``require_authenticated`` /
    ``get_user``); defaults stay open for parity with the other endpoints, so
    lock it down whenever the agent endpoint is.

    With the default :class:`NullTranscriptionBackend` a request returns ``410``
    (off): mount the view with a real backend to enable it.
    """

    def __init__(
        self,
        backend: TranscriptionBackend,
        *,
        require_authenticated: bool = False,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        config: AGUIConfig | None = None,
    ) -> None:
        self._backend = backend
        # Resolved once by AGUIServer; read per request these could only
        # ever be global, so two endpoints could not differ.
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # Mark this callable instance async so Django awaits ``__call__`` (see
        # DjangoAGUIView for the rationale); the backend operation is async.
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        # Establish + authorize the acting user first: this materializes
        # ``request.user`` off the event loop, so a backend that scopes by user
        # is loop-safe.
        deny = await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if isinstance(self._backend, NullTranscriptionBackend):
            return JsonResponse({"error": "transcription is disabled"}, status=410)
        # Parse the multipart body off the event loop — Django may spill a large
        # recording to a temp file, which is blocking I/O.
        audio = await sync_to_async(_read_audio)(request)
        if audio is None:
            return JsonResponse(
                {"error": "a single file under the 'audio' field is required"}, status=400
            )
        settings = self._config
        rejection = _validate(
            audio, settings.transcription_max_bytes, settings.transcription_allowed_types
        )
        if rejection is not None:
            return rejection
        text = await self._backend.transcribe(audio, request=request)
        return JsonResponse({"text": text})


def _read_audio(request: HttpRequest) -> UploadedFile | None:
    """The single uploaded clip under the ``audio`` field, or ``None``.

    Returns ``None`` for zero files (nothing posted) or more than one (the
    composer records one clip per request), so the caller answers ``400``.
    """
    files = request.FILES.getlist("audio")
    if len(files) != 1:
        return None
    return files[0]


def _validate(
    audio: UploadedFile, max_bytes: int, allowed_types: tuple[str, ...]
) -> JsonResponse | None:
    """Enforce the configured size cap + type allowlist; ``None`` when accepted.

    ``max_bytes`` of ``0`` disables the size cap; an empty ``allowed_types``
    accepts any declared content type. The content type is client-declared, so
    it is a coarse filter — the backend decides what to do with the bytes.
    """
    size = audio.size or 0
    if max_bytes and size > max_bytes:
        return JsonResponse({"error": f"audio exceeds the {max_bytes}-byte limit"}, status=413)
    if allowed_types and (audio.content_type or "") not in allowed_types:
        return JsonResponse(
            {"error": f"content type {audio.content_type!r} is not allowed"}, status=415
        )
    return None


__all__ = ["TranscribeView"]
