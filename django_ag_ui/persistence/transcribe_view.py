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
from django_pydantic_agent.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.agent.types.throttle import Throttle
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.capped_upload_handler import CappedUploadHandler
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend
from django_ag_ui.reject_async_throttle import reject_async_throttle
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated


class TranscribeView:
    """Owner-scoped speech-to-text endpoint (async, multipart in / JSON out).

    Mounted by [`AGUIServer`][django_ag_ui.AGUIServer] whenever
    ``transcription_backend=`` is a live
    [`TranscriptionBackend`][django_ag_ui.TranscriptionBackend]:

    - ``POST <prefix>transcribe/`` → multipart audio under the ``audio`` field;
      validates size/type from ``DJANGO_AG_UI`` settings, runs the backend, and
      returns ``200`` with ``{"text": "<transcript>"}``.

    The audio is transcribed and discarded — nothing is stored — so unlike
    [`AttachmentsView`][django_ag_ui.AttachmentsView] there is
    no download/delete route. The view carries the same authentication seam as
    [`DjangoAGUIView`][django_ag_ui.DjangoAGUIView] (``require_authenticated`` /
    ``get_user``), closed by default — which matters here beyond consistency:
    the backend spends money per request, so an open route is a bill as well as
    a leak.

    **Authentication is not a spend limit**, which is why ``throttle`` is here
    too: an authenticated caller looping small valid clips reaches the provider
    on every request. It takes the same [`Throttle`][django_ag_ui.Throttle] the
    agent endpoint takes and runs at the same point — after authentication, so a
    limiter can key on the acting user, and before the body is parsed. Give it
    its *own* limiter rather than sharing the agent's: one instance is one
    counter, so a shared one would let voice input consume the run budget.

    **The size cap aborts the upload rather than measuring it afterwards.** A
    ``CappedUploadHandler`` is inserted before the multipart body is parsed, so a
    clip over ``TRANSCRIPTION_MAX_BYTES`` is refused mid-stream instead of being
    spooled to a temp file in full and answered ``413`` once it is already on
    disk.

    With the default
    [`NullTranscriptionBackend`][django_ag_ui.NullTranscriptionBackend] a request
    returns ``410`` (off): mount the view with a real backend to enable it.
    """

    def __init__(
        self,
        backend: TranscriptionBackend,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
        throttle: Throttle | None = None,
        config: AGUIConfig | None = None,
    ) -> None:
        self._backend = backend
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        reject_async_throttle(throttle, allows="clip")
        self._throttle = throttle
        # Load-bearing here, unlike on the read-only catalogs: the only route is
        # POST, so CsrfViewMiddleware checks every request this view serves and a
        # token-less client could not reach the backend at all.
        warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        # Mark this callable instance async so Django awaits ``__call__``.
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        # First, so ``request.user`` is materialized off the event loop and a
        # backend that scopes by user is loop-safe.
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
        throttled = await self._throttled(request)
        if throttled is not None:
            return throttled
        settings = self._config
        # Before parsing, so an oversized clip aborts mid-stream rather than
        # spooling the whole body to a temp file first.
        guard = CappedUploadHandler(settings.transcription_max_bytes)
        request.upload_handlers.insert(0, guard)
        # Off the event loop: Django may spill an in-cap recording to a temp
        # file, which is blocking I/O.
        audio = await sync_to_async(_read_audio)(request)
        if guard.exceeded:
            return JsonResponse(
                {"error": f"audio exceeds the {settings.transcription_max_bytes}-byte limit"},
                status=413,
            )
        if audio is None:
            return JsonResponse(
                {"error": "a single file under the 'audio' field is required"}, status=400
            )
        rejection = _validate_type(audio, settings.transcription_allowed_types)
        if rejection is not None:
            return rejection
        text = await self._backend.transcribe(audio, request=request)
        return JsonResponse({"text": text})

    async def _throttled(self, request: HttpRequest) -> HttpResponseBase | None:
        """Apply the ``throttle`` hook, or ``None`` when the clip may proceed.

        The same shape and the same ordering as the agent endpoint's, so a
        project protecting both writes one kind of limiter and reads one kind of
        429.
        """
        if self._throttle is None:
            return None
        retry_after = await sync_to_async(self._throttle.consume, thread_sensitive=True)(request)
        if retry_after is None:
            return None
        response = JsonResponse({"error": "rate limited", "retry_after": retry_after}, status=429)
        response["Retry-After"] = str(retry_after)
        return response


def _read_audio(request: HttpRequest) -> UploadedFile | None:
    """The single uploaded clip under the ``audio`` field, or ``None``.

    Returns ``None`` for zero files (nothing posted) or more than one (the
    composer records one clip per request), so the caller answers ``400``.
    """
    files = request.FILES.getlist("audio")
    if len(files) != 1:
        return None
    return files[0]


def _validate_type(audio: UploadedFile, allowed_types: tuple[str, ...]) -> JsonResponse | None:
    """Enforce the configured type allowlist; ``None`` when accepted.

    An empty ``allowed_types`` accepts any declared content type. The content
    type is client-declared, so it is a coarse filter — the backend decides what
    to do with the bytes. Size is not checked here: the upload handler already
    refused anything over the cap while the body was still arriving.
    """
    if allowed_types and (audio.content_type or "") not in allowed_types:
        return JsonResponse(
            {"error": f"content type {audio.content_type!r} is not allowed"}, status=415
        )
    return None


__all__ = ["TranscribeView"]
