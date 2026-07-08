from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore


async def test_open_returns_none() -> None:
    store = NullAttachmentStore()
    assert await store.open("a1", request=RequestFactory().get("/")) is None


async def test_delete_is_noop() -> None:
    store = NullAttachmentStore()
    assert await store.delete("a1", request=RequestFactory().get("/")) is None


async def test_save_raises_to_fail_loud() -> None:
    store = NullAttachmentStore()
    upload = SimpleUploadedFile("notes.txt", b"hi", content_type="text/plain")
    with pytest.raises(NotImplementedError):
        await store.save(upload, request=RequestFactory().post("/"))
