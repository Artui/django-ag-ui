from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from django_ag_ui.persistence.anonymous_operation_error import AnonymousOperationError
from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment


class _FakeStore:
    """A minimal in-memory store exercising the view without a DB."""

    def __init__(
        self, *, ref: AttachmentRef | None = None, opened: OpenedAttachment | None = None
    ) -> None:
        self.ref = ref or AttachmentRef(id="a1", name="notes.txt", mime="text/plain", size=2)
        self.opened = opened
        self.saved: list[Any] = []
        self.deleted: list[str] = []

    async def save(self, upload: Any, *, request: HttpRequest) -> AttachmentRef:
        self.saved.append(upload)
        return self.ref

    async def open(self, attachment_id: str, *, request: HttpRequest) -> OpenedAttachment | None:
        return self.opened

    async def delete(self, attachment_id: str, *, request: HttpRequest) -> None:
        self.deleted.append(attachment_id)


def _upload_request(
    *, files: dict[str, Any] | None = None, content: bytes = b"hi", content_type: str = "text/plain"
) -> HttpRequest:
    data = (
        files
        if files is not None
        else {"file": SimpleUploadedFile("notes.txt", content, content_type=content_type)}
    )
    return RequestFactory().post("/agent/attachments/", data=data)


def _body(response: Any) -> Any:
    return json.loads(response.content)


async def test_anonymous_operation_refused_is_403() -> None:
    class _RefusingStore(_FakeStore):
        async def save(self, upload: Any, *, request: HttpRequest) -> AttachmentRef:
            raise AnonymousOperationError("anonymous refused")

    response = await AttachmentsView(_RefusingStore())(_upload_request())
    assert response.status_code == 403
    assert _body(response) == {"error": "forbidden"}


async def test_upload_returns_201_ref() -> None:
    store = _FakeStore()
    response = await AttachmentsView(store)(_upload_request())
    assert response.status_code == 201
    assert _body(response) == {"id": "a1", "name": "notes.txt", "mime": "text/plain", "size": 2}
    assert len(store.saved) == 1


async def test_upload_includes_url_when_store_sets_one() -> None:
    store = _FakeStore(
        ref=AttachmentRef(id="a1", name="n", mime="text/plain", size=2, url="/d/a1/")
    )
    response = await AttachmentsView(store)(_upload_request())
    assert _body(response)["url"] == "/d/a1/"


async def test_upload_disabled_with_null_store_is_410() -> None:
    response = await AttachmentsView(NullAttachmentStore())(_upload_request())
    assert response.status_code == 410
    assert _body(response) == {"error": "attachments are disabled"}


async def test_upload_without_file_is_400() -> None:
    response = await AttachmentsView(_FakeStore())(_upload_request(files={}))
    assert response.status_code == 400


async def test_upload_with_multiple_files_is_400() -> None:
    files = {
        "file": [
            SimpleUploadedFile("a.txt", b"a", content_type="text/plain"),
            SimpleUploadedFile("b.txt", b"b", content_type="text/plain"),
        ]
    }
    response = await AttachmentsView(_FakeStore())(_upload_request(files=files))
    assert response.status_code == 400


@override_settings(DJANGO_AG_UI={"ATTACHMENT_MAX_BYTES": 1})
async def test_upload_oversize_is_413() -> None:
    response = await AttachmentsView(_FakeStore())(_upload_request(content=b"too big"))
    assert response.status_code == 413
    assert "limit" in _body(response)["error"]


@override_settings(DJANGO_AG_UI={"ATTACHMENT_MAX_BYTES": 0})
async def test_max_bytes_zero_disables_the_cap() -> None:
    response = await AttachmentsView(_FakeStore())(_upload_request(content=b"x" * 1000))
    assert response.status_code == 201


async def test_upload_empty_file_passes_the_size_check() -> None:
    # A zero-byte upload exercises the falsy ``size`` branch in validation.
    response = await AttachmentsView(_FakeStore())(_upload_request(content=b""))
    assert response.status_code == 201


@override_settings(DJANGO_AG_UI={"ATTACHMENT_ALLOWED_TYPES": ["text/plain"]})
async def test_upload_disallowed_type_is_415() -> None:
    response = await AttachmentsView(_FakeStore())(_upload_request(content_type="image/png"))
    assert response.status_code == 415


@override_settings(DJANGO_AG_UI={"ATTACHMENT_ALLOWED_TYPES": ["text/plain"]})
async def test_upload_allowed_type_passes() -> None:
    response = await AttachmentsView(_FakeStore())(_upload_request(content_type="text/plain"))
    assert response.status_code == 201


async def test_upload_rejects_non_post() -> None:
    response = await AttachmentsView(_FakeStore())(RequestFactory().get("/agent/attachments/"))
    assert response.status_code == 405


async def test_download_streams_bytes_as_attachment() -> None:
    store = _FakeStore(
        opened=OpenedAttachment(
            ref=AttachmentRef(id="a1", name="notes.txt", mime="text/plain", size=5),
            content=b"hello",
        )
    )
    response = await AttachmentsView(store)(
        RequestFactory().get("/agent/attachments/a1/"), attachment_id="a1"
    )
    assert response.status_code == 200
    assert response.content == b"hello"
    assert response["Content-Type"] == "text/plain"
    assert response["Content-Disposition"] == 'attachment; filename="notes.txt"'
    assert response["X-Content-Type-Options"] == "nosniff"


async def test_download_empty_mime_falls_back_to_octet_stream() -> None:
    store = _FakeStore(
        opened=OpenedAttachment(ref=AttachmentRef(id="a1", name='"', mime="", size=1), content=b"x")
    )
    response = await AttachmentsView(store)(
        RequestFactory().get("/agent/attachments/a1/"), attachment_id="a1"
    )
    assert response["Content-Type"] == "application/octet-stream"
    # The quote-only name is sanitised to a safe fallback.
    assert response["Content-Disposition"] == 'attachment; filename="attachment"'


async def test_download_missing_is_404() -> None:
    response = await AttachmentsView(_FakeStore())(
        RequestFactory().get("/agent/attachments/absent/"), attachment_id="absent"
    )
    assert response.status_code == 404


async def test_delete_removes_attachment() -> None:
    store = _FakeStore()
    response = await AttachmentsView(store)(
        RequestFactory().delete("/agent/attachments/a1/"), attachment_id="a1"
    )
    assert response.status_code == 204
    assert store.deleted == ["a1"]


async def test_detail_rejects_unsupported_method() -> None:
    response = await AttachmentsView(_FakeStore())(
        RequestFactory().put("/agent/attachments/a1/"), attachment_id="a1"
    )
    assert response.status_code == 405


async def test_anonymous_rejected_when_require_authenticated() -> None:
    view = AttachmentsView(_FakeStore(), require_authenticated=True)
    response = await view(_upload_request())
    assert response.status_code == 401


async def test_get_user_hook_opens_the_endpoint() -> None:
    view = AttachmentsView(
        _FakeStore(),
        require_authenticated=True,
        get_user=lambda _request: SimpleNamespace(is_authenticated=True),
    )
    response = await view(_upload_request())
    assert response.status_code == 201
