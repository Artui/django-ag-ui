from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction, sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import (
    FileResponse,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.response import HttpResponseBase
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.null_attachment_store import NullAttachmentStore
from django_spec_agents.persistence.types.attachment_ref import AttachmentRef
from django_spec_agents.persistence.types.attachment_store import AttachmentStore
from django_spec_agents.persistence.types.opened_attachment import OpenedAttachment
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.capped_upload_handler import CappedUploadHandler
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated


class AttachmentsView:
    """Owner-scoped file-upload + download endpoint (async, multipart/JSON)."""

    def __init__(
        self,
        store: AttachmentStore,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
        config: AGUIConfig | None = None,
    ) -> None:
        self._store = store
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # Load-bearing here: upload is POST and delete is DELETE, so CSRF checks both.
        warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        markcoroutinefunction(cast("Any", self))

    async def __call__(
        self, request: HttpRequest, attachment_id: str | None = None
    ) -> HttpResponseBase:
        # First, so ``request.user`` is materialized before the store's loop-safe calls.
        deny = await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        try:
            if attachment_id is None:
                return await self._upload(request)
            return await self._detail(request, attachment_id)
        except AnonymousOperationError:
            return auth_error_response(403)

    async def _upload(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if isinstance(self._store, NullAttachmentStore):
            return JsonResponse({"error": "attachments are disabled"}, status=410)
        settings = self._config
        # Before parsing, so an oversized upload aborts mid-stream, not after a full spool.
        guard = CappedUploadHandler(settings.attachment_max_bytes)
        request.upload_handlers.insert(0, guard)
        upload = await sync_to_async(_read_upload)(request)
        if guard.exceeded:
            return JsonResponse(
                {"error": f"file exceeds the {settings.attachment_max_bytes}-byte limit"},
                status=413,
            )
        if upload is None:
            return JsonResponse(
                {"error": "a single file under the 'file' field is required"}, status=400
            )
        rejection = _validate_type(upload, settings.attachment_allowed_types)
        if rejection is not None:
            return rejection
        ref = await self._store.save(upload, request=request)
        return JsonResponse(_ref_to_json(ref), status=201)

    async def _detail(self, request: HttpRequest, attachment_id: str) -> HttpResponseBase:
        if request.method == "GET":
            opened = await self._store.open(attachment_id, request=request)
            if opened is None:
                return JsonResponse({"error": "not found"}, status=404)
            return _download_response(opened)
        if request.method == "DELETE":
            await self._store.delete(attachment_id, request=request)
            return HttpResponse(status=204)
        return HttpResponseNotAllowed(["GET", "DELETE"])


def _read_upload(request: HttpRequest) -> UploadedFile | None:
    """The single uploaded file under the ``file`` field, or ``None``."""
    files = request.FILES.getlist("file")
    if len(files) != 1:
        return None
    return files[0]


def _validate_type(upload: UploadedFile, allowed_types: tuple[str, ...]) -> JsonResponse | None:
    """Enforce the content-type allowlist; ``None`` when accepted."""
    if allowed_types and (upload.content_type or "") not in allowed_types:
        return JsonResponse(
            {"error": f"content type {upload.content_type!r} is not allowed"}, status=415
        )
    return None


def _ref_to_json(ref: AttachmentRef) -> dict[str, Any]:
    """The wire shape for an upload result; ``url`` only when the store set one."""
    data: dict[str, Any] = {"id": ref.id, "name": ref.name, "mime": ref.mime, "size": ref.size}
    if ref.url is not None:
        data["url"] = ref.url
    return data


def _download_response(opened: OpenedAttachment) -> HttpResponseBase:
    """Stream the file back as a download — never inline, never content-sniffed."""
    ref = opened.ref
    response = FileResponse(
        opened.content,
        as_attachment=True,
        filename=_safe_filename(ref.name),
        content_type=ref.mime or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _safe_filename(name: str) -> str:
    """A header-safe filename: no quotes or CR/LF that could split the header."""
    return name.replace('"', "").replace("\r", "").replace("\n", "") or "attachment"


__all__ = ["AttachmentsView"]
