from __future__ import annotations

import io
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory

from django_ag_ui.agent.attachment_toolset import build_attachment_toolset
from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment


class _FakeStore:
    def __init__(self, opened: OpenedAttachment | None) -> None:
        self.opened = opened
        self.opened_ids: list[str] = []

    async def open(self, attachment_id: str, *, request: HttpRequest) -> OpenedAttachment | None:
        self.opened_ids.append(attachment_id)
        return self.opened


def _read_attachment(store: Any, request: HttpRequest) -> Any:
    toolset = build_attachment_toolset(store, request)
    assert toolset.id == "django-ag-ui-attachments"
    return toolset.tools["read_attachment"].function


def _opened(mime: str, content: bytes, *, name: str = "f", size: int = 3) -> OpenedAttachment:
    return OpenedAttachment(
        ref=AttachmentRef(id="a1", name=name, mime=mime, size=size), content=io.BytesIO(content)
    )


async def test_reads_textual_attachment_as_text() -> None:
    request = RequestFactory().get("/")
    store = _FakeStore(_opened("text/plain", b"hello world"))
    read = _read_attachment(store, request)
    assert await read("a1") == "hello world"
    assert store.opened_ids == ["a1"]


async def test_reads_non_text_mime_as_a_manifest() -> None:
    store = _FakeStore(_opened("image/png", b"\x89PNG\r\n", name="logo.png", size=6))
    read = _read_attachment(store, RequestFactory().get("/"))
    result = await read("a1")
    assert "logo.png" in result
    assert "image/png" in result
    assert "6 bytes" in result


async def test_textual_mime_but_undecodable_bytes_is_a_manifest() -> None:
    # ``text/plain`` content type, but the bytes aren't valid UTF-8.
    store = _FakeStore(_opened("text/plain", b"\xff\xfe", name="bad.txt", size=2))
    read = _read_attachment(store, RequestFactory().get("/"))
    result = await read("a1")
    assert "bad.txt" in result
    assert "not text" in result


async def test_missing_attachment_reports_clearly() -> None:
    store = _FakeStore(None)
    read = _read_attachment(store, RequestFactory().get("/"))
    assert await read("ghost") == "No attachment with id 'ghost' is available."


async def test_empty_mime_renders_as_binary() -> None:
    store = _FakeStore(_opened("", b"\x00\x01", name="blob", size=2))
    read = _read_attachment(store, RequestFactory().get("/"))
    assert "binary" in await read("a1")
