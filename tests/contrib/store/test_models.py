from __future__ import annotations

from django_ag_ui.contrib.store.models import StoredAttachment, StoredConversation


def test_str_prefers_title() -> None:
    assert str(StoredConversation(thread_id="t1", title="Trip planning")) == "Trip planning"


def test_str_falls_back_to_thread_id() -> None:
    assert str(StoredConversation(thread_id="t1", title="")) == "t1"


def test_attachment_str_prefers_name() -> None:
    assert str(StoredAttachment(attachment_id="a1", name="notes.txt")) == "notes.txt"


def test_attachment_str_falls_back_to_id() -> None:
    assert str(StoredAttachment(attachment_id="a1", name="")) == "a1"
