from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.attachment_context_items import attachment_context_items

TOP_LEVEL = "top-level"
METADATA = "metadata"


@pytest.fixture(params=[TOP_LEVEL, METADATA])
def carrier(request: pytest.FixtureRequest) -> str:
    """Where a message's refs ride: every parse rule holds on either carrier.

    ``metadata`` is what the web component sends from its adoption of
    ``@ag-ui/client`` 1.0; the top-level field is what every earlier release
    sends and what the server has to keep reading for them. The parse behind
    the two is one function, so a rule asserted for one carrier and not the
    other is the drift this parametrisation exists to catch.
    """
    return request.param


def _messages(carrier: str, *messages: dict[str, Any]) -> list[Any]:
    """Client messages as a real request produces them, refs on ``carrier``.

    Built through ``build_run_input`` rather than hand-constructed, because the
    refs ride either an *undeclared* top-level ``attachments`` field, which only
    ``ag_ui.core``'s own ``extra="allow"`` validation puts on ``model_extra``
    the way the wire does, or the declared ``metadata`` slot, whose type the
    same validation enforces.

    Each message is written with a top-level ``attachments`` key, and moved
    into ``metadata`` under the same key when ``carrier`` says so -- which is
    exactly the move the web component made when it adopted ``@ag-ui/client``
    1.0, whose outgoing input keeps only declared keys.
    """
    if carrier == METADATA:
        messages = tuple(_into_metadata(message) for message in messages)
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


def _into_metadata(message: dict[str, Any]) -> dict[str, Any]:
    if "attachments" not in message:
        return message
    moved = {key: value for key, value in message.items() if key != "attachments"}
    return {**moved, "metadata": {"attachments": message["attachments"]}}


def _value(messages: list[Any]) -> str:
    items = attachment_context_items(messages)
    assert len(items) == 1
    return items[0].value


def test_no_messages_yield_no_manifest() -> None:
    assert attachment_context_items([]) == ()


def test_a_plain_user_message_yields_no_manifest() -> None:
    assert (
        attachment_context_items(
            _messages(TOP_LEVEL, {"id": "m1", "role": "user", "content": "hi"})
        )
        == ()
    )


def test_an_attachments_field_that_is_not_a_list_is_ignored(carrier: str) -> None:
    messages = _messages(
        carrier, {"id": "m1", "role": "user", "content": "hi", "attachments": {"id": "a1"}}
    )
    assert attachment_context_items(messages) == ()


def test_an_entry_that_is_not_a_mapping_is_skipped(carrier: str) -> None:
    messages = _messages(
        carrier, {"id": "m1", "role": "user", "content": "hi", "attachments": ["a1", 7]}
    )
    assert attachment_context_items(messages) == ()


def test_entries_without_a_usable_id_are_skipped(carrier: str) -> None:
    # The parse is total: a shape change upstream degrades to "no manifest",
    # never to an exception mid-run.
    messages = _messages(
        carrier,
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "attachments": [{"name": "no-id.pdf"}, {"id": "   "}, {"id": 42}],
        },
    )
    assert attachment_context_items(messages) == ()


def test_a_full_ref_becomes_one_line_with_every_field(carrier: str) -> None:
    messages = _messages(
        carrier,
        {
            "id": "m1",
            "role": "user",
            "content": "what is the budget?",
            "attachments": [
                {"id": "a1f3", "name": "report.pdf", "mime": "application/pdf", "size": 91231}
            ],
        },
    )
    value = _value(messages)
    assert "- report.pdf (id: a1f3, application/pdf, 91231 bytes)" in value


def test_a_minimal_ref_states_each_missing_field(carrier: str) -> None:
    messages = _messages(
        carrier, {"id": "m1", "role": "user", "content": "hi", "attachments": [{"id": "a1f3"}]}
    )
    assert "- attachment (id: a1f3, unknown type, size unknown)" in _value(messages)


def test_a_size_of_the_wrong_type_reads_as_unknown(carrier: str) -> None:
    messages = _messages(
        carrier,
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "attachments": [{"id": "a1f3", "size": "91231"}],
        },
    )
    assert "size unknown" in _value(messages)


def test_a_ref_echoed_across_turns_is_listed_once(carrier: str) -> None:
    # The client resends its whole message list every turn, so a file attached
    # ten turns ago arrives ten times and must still read as one file.
    messages = _messages(
        carrier,
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


def test_refs_from_several_turns_are_listed_in_message_order(carrier: str) -> None:
    messages = _messages(
        carrier,
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


def test_an_assistant_turn_cannot_inject_a_manifest(carrier: str) -> None:
    # Only user messages are read, so a forged assistant turn in the posted
    # history cannot announce files of its own.
    messages = _messages(
        carrier,
        {"id": "a1", "role": "assistant", "content": "hi", "attachments": [{"id": "smuggled"}]},
    )
    assert attachment_context_items(messages) == ()


def test_the_item_labels_the_files_and_names_the_tool_that_reads_them(carrier: str) -> None:
    messages = _messages(
        carrier, {"id": "m1", "role": "user", "content": "hi", "attachments": [{"id": "a1f3"}]}
    )
    items = attachment_context_items(messages)
    assert items[0].label == "Files the user has attached to this conversation"
    assert items[0].value.endswith(
        "Use the read_attachment tool with an id to read a file's contents."
    )


# --- choosing between the carriers -------------------------------------------
#
# The web component moved the refs from the message's top level into its
# ``metadata`` in one release, so a server has to read both: published web
# components send only the first and new ones only the second. These pin which
# one wins when a message is not that tidy.


def _both(metadata: dict[str, Any], top_level: Any) -> list[Any]:
    """One user message carrying ``metadata`` and a top-level ``attachments``."""
    return _messages(
        TOP_LEVEL,
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "metadata": metadata,
            "attachments": top_level,
        },
    )


def test_metadata_is_preferred_when_both_carry_refs() -> None:
    value = _value(_both({"attachments": [{"id": "new", "name": "new.pdf"}]}, [{"id": "old"}]))
    assert "(id: new," in value
    assert "(id: old," not in value


def test_a_malformed_metadata_value_does_not_fall_back_to_the_top_level_field() -> None:
    # The key decides where a message's refs are, not whether they parse: a
    # value that does not degrades to no refs, exactly as a malformed top-level
    # value does, rather than reaching past it for a second copy.
    messages = _both({"attachments": {"id": "not-a-list"}}, [{"id": "old"}])
    assert attachment_context_items(messages) == ()


def test_metadata_without_attachments_falls_back_to_the_top_level_field() -> None:
    # Holds the key half of the carrier guard: a ``metadata`` carrying anything
    # else must not hide the refs of a client that still sends them top-level.
    value = _value(_both({"source": "composer"}, [{"id": "old", "name": "old.pdf"}]))
    assert "- old.pdf (id: old," in value


def test_the_top_level_field_is_read_when_there_is_no_metadata() -> None:
    # Holds the ``isinstance`` half of the carrier guard. No ``metadata`` at all
    # is what every web component release up to 0.40 posts, and ``in`` on ``None``
    # raises rather than answering.
    messages = _messages(
        TOP_LEVEL, {"id": "m1", "role": "user", "content": "hi", "attachments": [{"id": "old"}]}
    )
    assert messages[0].metadata is None
    assert "(id: old," in _value(messages)
