from __future__ import annotations

from datetime import datetime, timezone

from django_ag_ui.persistence.types.conversation_meta import ConversationMeta


def test_defaults() -> None:
    meta = ConversationMeta(thread_id="t1", title="Hello")
    assert meta.thread_id == "t1"
    assert meta.title == "Hello"
    assert meta.updated_at is None
    assert meta.preview == ""
    assert meta.owner_id is None


def test_all_fields() -> None:
    when = datetime(2026, 6, 24, tzinfo=timezone.utc)
    meta = ConversationMeta(
        thread_id="t1", title="Hi", updated_at=when, preview="how are you", owner_id="7"
    )
    assert meta.updated_at is when
    assert meta.preview == "how are you"
    assert meta.owner_id == "7"
