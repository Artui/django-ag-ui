from __future__ import annotations

import warnings
from typing import Any

from ag_ui.core import AssistantMessage, Message, ToolMessage, UserMessage

from django_ag_ui.agent.strip_binary_content import strip_binary_content

_PDF_B64 = "JVBERi0xLjQgZmFrZQ=="


def _text_part() -> dict[str, Any]:
    return {"type": "text", "text": "here is the file"}


def _document_part() -> dict[str, Any]:
    return {
        "type": "document",
        "source": {"type": "data", "value": _PDF_B64, "mimeType": "application/pdf"},
    }


def _url_image_part() -> dict[str, Any]:
    return {"type": "image", "source": {"type": "url", "value": "https://example.test/x.png"}}


def _user(content: Any, **extra: Any) -> UserMessage:
    return UserMessage.model_validate({"id": "m1", "role": "user", "content": content, **extra})


def _binary_part(**fields: Any) -> Any:
    """A deprecated ``binary`` part, built without tripping its own warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return _user([{"type": "binary", "mimeType": "application/pdf", **fields}]).content[0]


def test_an_empty_message_list_stays_empty() -> None:
    assert strip_binary_content([]) == []


def test_a_string_content_message_is_passed_through_untouched() -> None:
    # The overwhelmingly common case, and the one the whole change must not
    # disturb: the composer posts string content. Identity, not equality — a
    # copy here would be pure cost on every message of every turn.
    message = _user("what is in the file?")

    (kept,) = strip_binary_content([message])

    assert kept is message


def test_every_message_role_survives_a_strip() -> None:
    messages: list[Message] = [
        _user("hi"),
        AssistantMessage(id="a1", role="assistant", content="hello"),
        ToolMessage(id="t1", role="tool", content="Attached below: report.pdf", tool_call_id="c1"),
    ]

    assert strip_binary_content(messages) == messages


def test_a_message_of_text_parts_only_is_returned_unchanged() -> None:
    message = _user([_text_part()])

    (kept,) = strip_binary_content([message])

    assert kept is message


def test_a_mixed_message_keeps_its_text_and_loses_its_bytes() -> None:
    message = _user([_text_part(), _document_part()])

    (kept,) = strip_binary_content([message])

    assert [part.type for part in kept.content] == ["text"]
    assert _PDF_B64 not in kept.model_dump_json()


def test_a_message_that_is_only_bytes_is_dropped_entirely() -> None:
    # The shape ``read_attachment``'s ToolReturn serialises into: a synthetic
    # user message carrying the document and nothing else. Emptied of it there
    # is no message left, and an empty user turn is a blank chat bubble.
    assert strip_binary_content([_user([_document_part()])]) == []


def test_a_url_referenced_part_is_a_reference_and_is_kept() -> None:
    # Dropping it would lose information and save no storage: the bytes are not
    # in the row, only a pointer to them.
    message = _user([_url_image_part()])

    (kept,) = strip_binary_content([message])

    assert kept is message


def test_the_deprecated_binary_part_is_dropped_when_it_carries_data() -> None:
    message = _user("caption").model_copy(
        update={"content": [_binary_part(data=_PDF_B64)]},
    )

    assert strip_binary_content([message]) == []


def test_the_deprecated_binary_part_is_kept_when_it_only_references() -> None:
    message = _user("caption").model_copy(
        update={"content": [_binary_part(url="https://example.test/x.pdf")]},
    )

    (kept,) = strip_binary_content([message])

    assert kept is message


def test_ids_and_the_attachments_extra_survive_the_strip() -> None:
    """The guard on the whole change.

    Stripping by re-validating through ``AGUIAdapter.load_messages`` /
    ``dump_messages`` is the convenient implementation and the wrong one: the
    round-trip regenerates every message id and discards ``model_extra``,
    silently reverting the fix that keeps a client's attachment refs resolvable
    after a reload. ``model_copy`` is what this asserts.
    """
    attachments = [{"id": "a1", "name": "report.pdf", "mime": "application/pdf", "size": 2300}]
    message = _user([_text_part(), _document_part()], attachments=attachments)

    (kept,) = strip_binary_content([message])

    assert kept.id == "m1"
    assert kept.model_extra == {"attachments": attachments}
    assert kept.model_dump(by_alias=True)["attachments"] == attachments


def test_only_the_emptied_message_is_dropped_from_a_thread() -> None:
    kept_before = _user("what is in the file?")
    tool_note = ToolMessage(
        id="t1",
        role="tool",
        content="Attached below: report.pdf",
        tool_call_id="c1",
    )

    stripped = strip_binary_content([kept_before, tool_note, _user([_document_part()])])

    assert stripped == [kept_before, tool_note]
