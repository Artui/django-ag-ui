from __future__ import annotations

from django_ag_ui import A2UI_ACTIVITY_TYPE, a2ui_activity

OPERATIONS = ({"version": "v0.9", "createSurface": {"surfaceId": "s", "catalogId": "c"}},)


def test_the_event_carries_the_operations_whole_and_replaces() -> None:
    event = a2ui_activity(OPERATIONS, message_id="a2ui-surface-call-1")
    assert event.model_dump(by_alias=True, exclude_none=True) == {
        "type": "ACTIVITY_SNAPSHOT",
        "messageId": "a2ui-surface-call-1",
        "activityType": A2UI_ACTIVITY_TYPE,
        "content": {"a2ui_operations": list(OPERATIONS)},
        "replace": True,
    }


def test_without_an_id_each_event_is_a_new_prefixed_surface() -> None:
    first, second = a2ui_activity(OPERATIONS), a2ui_activity(OPERATIONS)
    assert first.message_id.startswith("a2ui-surface-")
    assert first.message_id != second.message_id
