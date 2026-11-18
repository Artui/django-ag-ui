from __future__ import annotations

from ag_ui.core import AssistantMessage, ToolMessage

from django_ag_ui.agent.lend_answered_by import lend_answered_by


def _tool(call: str, metadata: dict | None = None) -> ToolMessage:
    return ToolMessage(id=f"id-{call}", tool_call_id=call, content="ok", metadata=metadata)


def test_a_stored_mark_is_lent_to_the_same_call() -> None:
    out = lend_answered_by([_tool("a")], [_tool("a", {"answered_by": "reviewer:m"})])
    assert out[0].metadata == {"answered_by": "reviewer:m"}


def test_other_calls_and_other_messages_are_left_alone() -> None:
    assistant = AssistantMessage(id="x", content="hi")
    out = lend_answered_by([assistant, _tool("b")], [_tool("a", {"answered_by": "r"})])
    assert out[0] is assistant
    assert out[1].metadata is None


def test_a_non_string_or_missing_mark_is_not_lent() -> None:
    stored = [_tool("a", {"answered_by": 1}), _tool("b", {"x": 1}), _tool("c")]
    out = lend_answered_by([_tool("a"), _tool("b"), _tool("c")], stored)
    assert [m.metadata for m in out] == [None, None, None]


def test_metadata_the_message_has_is_kept() -> None:
    out = lend_answered_by([_tool("a", {"k": 1})], [_tool("a", {"answered_by": "r"})])
    assert out[0].metadata == {"k": 1, "answered_by": "r"}
