from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from django_ag_ui.contrib.store.default_attachment_store import DefaultAttachmentStore
from django_ag_ui.contrib.store.models import StoredAttachment

pytestmark = pytest.mark.django_db


def _upload(
    name: str | None = "notes.txt",
    content: bytes = b"hello",
    content_type: str | None = "text/plain",
) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


def test_save_then_open_round_trips() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    assert ref.name == "notes.txt"
    assert ref.mime == "text/plain"
    assert ref.size == 5

    opened = store._open(ref.id, "7")
    assert opened is not None
    assert opened.content == b"hello"
    assert opened.ref.id == ref.id
    assert opened.ref.name == "notes.txt"


def test_open_missing_returns_none() -> None:
    assert DefaultAttachmentStore()._open("absent", "7") is None


def test_open_is_owner_scoped() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    # A different owner can't resolve the same id.
    assert store._open(ref.id, "99") is None


def test_save_strips_path_from_name() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(name="sub/dir/notes.txt"), "7")
    assert ref.name == "notes.txt"


def test_save_without_name_falls_back() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(name=None), "7")
    assert ref.name == "attachment"


def test_save_without_content_type_stores_empty_mime() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(content_type=None), "7")
    assert ref.mime == ""


def test_save_empty_file_has_zero_size() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(content=b""), "7")
    assert ref.size == 0


def test_anonymous_owner_normalized_to_empty_string() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), None)
    assert store._open(ref.id, None) is not None
    assert StoredAttachment.objects.get(attachment_id=ref.id).owner_id == ""


def test_remove_deletes_row_and_bytes() -> None:
    store = DefaultAttachmentStore()
    ref = store._save(_upload(), "7")
    store._remove(ref.id, "7")
    assert store._open(ref.id, "7") is None
    assert not StoredAttachment.objects.filter(attachment_id=ref.id).exists()


def test_remove_missing_is_noop() -> None:
    # No row for this id — must not raise.
    assert DefaultAttachmentStore()._remove("absent", "7") is None
