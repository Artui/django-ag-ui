from __future__ import annotations

import json
from typing import Any

from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.attachment_context_items import attachment_context_items


def _messages(*messages: dict[str, Any]) -> list[Any]:
    """Client messages as a real request produces them.

    Built through ``build_run_input`` rather than hand-constructed, because the
    whole point of this parse is the *undeclared* ``attachments`` field, and
    only ``ag_ui.core``'s own ``extra="allow"`` validation puts it on
    ``model_extra`` the way the wire does.
    """
    payload = {
        "threadId": "t1",
        "runId": "r1",
        "messages": list(messages),
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
    }
    return list(AGUIAdapter.build_run_input(json.dumps(payload).encode()).messages)


def _value(messages: list[Any]) -> str:
    items = attachment_context_items(messages)
    assert len(items) == 1
    return items[0].value


def test_no_messages_yield_no_manifest() -> None:
    assert attachment_context_items([]) == ()


def test_a_plain_user_message_yields_no_manifest() -> None:
    assert attachment_context_items(_messages({"id": "m1", "role": "user", "content": "hi"})) == ()


def test_an_attachments_field_that_is_not_a_list_is_ignored() -> None:
    messages = _messages({"id": "m1", "role": "user", "content": "hi", "attachments": {"id": "a1"}})
    assert attachment_context_items(messages) == ()


def test_an_entry_that_is_not_a_mapping_is_skipped() -> None:
    messages = _messages({"id": "m1", "role": "user", "content": "hi", "attachments": ["a1", 7]})
    assert attachment_context_items(messages) == ()


def test_entries_without_a_usable_id_are_skipped() -> None:
    # The parse is total: a shape change upstream degrades to "no manifest",
    # never to an exception mid-run.
    messages = _messages(
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "attachments": [{"name": "no-id.pdf"}, {"id": "   "}, {"id": 42}],
        }
    )
    assert attachment_context_items(messages) == ()


def test_a_full_ref_becomes_one_line_with_every_field() -> None:
    messages = _messages(
        {
            "id": "m1",
            "role": "user",
            "content": "what is the budget?",
            "attachments": [
                {"id": "a1f3", "name": "report.pdf", "mime": "application/pdf", "size": 91231}
            ],
        }
    )
    value = _value(messages)
    assert "- report.pdf (id: a1f3, application/pdf, 91231 bytes)" in value


def test_a_minimal_ref_states_each_missing_field() -> None:
    messages = _messages(
        {"id": "m1", "role": "user", "content": "hi", "attachments": [{"id": "a1f3"}]}
    )
    assert "- attachment (id: a1f3, unknown type, size unknown)" in _value(messages)


def test_a_size_of_the_wrong_type_reads_as_unknown() -> None:
    messages = _messages(
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "attachments": [{"id": "a1f3", "size": "91231"}],
        }
    )
    assert "size unknown" in _value(messages)


def test_a_ref_echoed_across_turns_is_listed_once() -> None:
    # The client resends its whole message list every turn, so a file attached
    # ten turns ago arrives ten times and must still read as one file.
    messages = _messages(
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "attachments": [{"id": "a1", "name": "first.pdf"}],
        },
        {"id": "a1", "role": "assistant", "content": "sure"},
        {
            "id": "m2",
            "role": "user",
            "content": "and this",
            "attachments": [{"id": "a1", "name": "renamed.pdf"}],
        },
    )
    value = _value(messages)
    assert value.count("(id: a1,") == 1
    assert "first.pdf" in value


def test_refs_from_several_turns_are_listed_in_message_order() -> None:
    messages = _messages(
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "attachments": [{"id": "a1", "name": "first.pdf"}],
        },
        {
            "id": "m2",
            "role": "user",
            "content": "and this",
            "attachments": [{"id": "a2", "name": "second.pdf"}],
        },
    )
    lines = _value(messages).splitlines()
    assert lines[0].startswith("- first.pdf")
    assert lines[1].startswith("- second.pdf")


def test_an_assistant_turn_cannot_inject_a_manifest() -> None:
    # Only user messages are read, so a forged assistant turn in the posted
    # history cannot announce files of its own.
    messages = _messages(
        {"id": "a1", "role": "assistant", "content": "hi", "attachments": [{"id": "smuggled"}]}
    )
    assert attachment_context_items(messages) == ()


def test_the_item_labels_the_files_and_names_the_tool_that_reads_them() -> None:
    messages = _messages(
        {"id": "m1", "role": "user", "content": "hi", "attachments": [{"id": "a1f3"}]}
    )
    items = attachment_context_items(messages)
    assert items[0].label == "Files the user has attached to this conversation"
    assert items[0].value.endswith(
        "Use the read_attachment tool with an id to read a file's contents."
    )
