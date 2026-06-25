from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction, sync_to_async
from django.core.files.uploadedfile import UploadedFile
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.response import HttpResponseBase

from django_ag_ui.conf import get_settings
from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.attachment_store import AttachmentStore
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment
from django_ag_ui.utils import GetUser, aauthorize


class AttachmentsView:
    """Owner-scoped file-upload + download endpoint (async, multipart/JSON).

    Mounted by :func:`~django_ag_ui.get_urls` with ``attachments=<store>`` over
    an :class:`~django_ag_ui.persistence.types.attachment_store.AttachmentStore`:

    - ``POST   <prefix>attachments/``      → multipart upload under the ``file``
      field; validates size/type from ``DJANGO_AG_UI`` settings, persists the
      bytes, and returns ``201`` with the :class:`AttachmentRef` JSON
      (``{"id", "name", "mime", "size", "url"?}``) — a durable *ref*, not bytes.
    - ``GET    <prefix>attachments/<id>/`` → stream the bytes back (owner-checked)
      for preview/download; missing or cross-owner → ``404``.
    - ``DELETE <prefix>attachments/<id>/`` → drop the attachment (``204``).

    Every operation is scoped to the acting user: the store filters by owner, so
    one user's id can never resolve another's file. Downloads are served as an
    ``attachment`` with ``X-Content-Type-Options: nosniff`` so an uploaded
    ``text/html`` can't execute as a same-origin page. The view carries the same
    authentication seam as :class:`~django_ag_ui.DjangoAGUIView`
    (``require_authenticated`` / ``get_user``); defaults stay open for parity
    with the catalog views, so lock it down whenever the agent endpoint is.

    With the default :class:`NullAttachmentStore` an upload returns ``410`` (off):
    mount the view with a real store to enable it.
    """

    def __init__(
        self,
        store: AttachmentStore,
        *,
        require_authenticated: bool = False,
        get_user: GetUser | None = None,
    ) -> None:
        self._store = store
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        # Mark this callable instance async so Django awaits ``__call__`` (see
        # DjangoAGUIView for the rationale); the store operations are async.
        markcoroutinefunction(cast("Any", self))

    async def __call__(
        self, request: HttpRequest, attachment_id: str | None = None
    ) -> HttpResponseBase:
        # Establish + authorize the acting user first: this materializes
        # ``request.user`` off the event loop, so the store's ``owner_id_for``
        # scoping is loop-safe on the calls below.
        if not await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
        ):
            return JsonResponse({"error": "authentication required"}, status=401)
        if attachment_id is None:
            return await self._upload(request)
        return await self._detail(request, attachment_id)

    async def _upload(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if isinstance(self._store, NullAttachmentStore):
            return JsonResponse({"error": "attachments are disabled"}, status=410)
        # Parse the multipart body off the event loop — for a large upload Django
        # spills it to a temp file, which is blocking I/O.
        upload = await sync_to_async(_read_upload)(request)
        if upload is None:
            return JsonResponse(
                {"error": "a single file under the 'file' field is required"}, status=400
            )
        settings = get_settings()
        rejection = _validate(
            upload, settings.attachment_max_bytes, settings.attachment_allowed_types
        )
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
    """The single uploaded file under the ``file`` field, or ``None``.

    Returns ``None`` for zero files (nothing posted) or more than one (the
    composer uploads one file per request), so the caller answers ``400``.
    """
    files = request.FILES.getlist("file")
    if len(files) != 1:
        return None
    return files[0]


def _validate(
    upload: UploadedFile, max_bytes: int, allowed_types: tuple[str, ...]
) -> JsonResponse | None:
    """Enforce the configured size cap + type allowlist; ``None`` when accepted.

    ``max_bytes`` of ``0`` disables the size cap; an empty ``allowed_types``
    accepts any declared content type. The content type is client-declared, so
    it is a coarse filter — the store decides what to do with the bytes.
    """
    size = upload.size or 0
    if max_bytes and size > max_bytes:
        return JsonResponse({"error": f"file exceeds the {max_bytes}-byte limit"}, status=413)
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


def _download_response(opened: OpenedAttachment) -> HttpResponse:
    """Stream bytes back as a download — never inline, never content-sniffed."""
    ref = opened.ref
    response = HttpResponse(opened.content, content_type=ref.mime or "application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{_safe_filename(ref.name)}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _safe_filename(name: str) -> str:
    """A header-safe filename: no quotes or CR/LF that could split the header."""
    return name.replace('"', "").replace("\r", "").replace("\n", "") or "attachment"


__all__ = ["AttachmentsView"]
