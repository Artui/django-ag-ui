from __future__ import annotations

from ag_ui.core import (
    AssistantMessage,
    FunctionCall,
    Message,
    ReasoningMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)

from django_ag_ui.agent.lend_stored_ids import lend_stored_ids


def _text(message_id: str, content: str) -> AssistantMessage:
    return AssistantMessage(id=message_id, role="assistant", content=content)


def _calls(message_id: str, *call_ids: str) -> AssistantMessage:
    return AssistantMessage(
        id=message_id,
        role="assistant",
        tool_calls=[
            ToolCall(id=call_id, type="function", function=FunctionCall(name="t", arguments="{}"))
            for call_id in call_ids
        ],
    )


def _tool(message_id: str, call_id: str, content: str = "r") -> ToolMessage:
    return ToolMessage(id=message_id, role="tool", tool_call_id=call_id, content=content)


def _ids(messages: list[Message]) -> list[str]:
    return [message.id for message in messages]


def test_each_kind_takes_the_id_of_the_same_stored_message() -> None:
    dumped = [
        UserMessage(id="u1", role="user", content="hi"),
        _calls("fresh-1", "call-a", "call-b"),
        _tool("fresh-2", "call-a"),
        _tool("fresh-3", "call-b"),
        ReasoningMessage(id="fresh-4", role="reasoning", content="pondering"),
        _text("fresh-5", "the answer"),
    ]
    stored = [
        UserMessage(id="u1", role="user", content="hi"),
        _calls("streamed-calls", "call-a", "call-b"),
        _tool("result-a", "call-a"),
        _tool("result-b", "call-b"),
        ReasoningMessage(id="streamed-reasoning", role="reasoning", content="pondering"),
        _text("streamed-answer", "the answer"),
    ]

    lent = lend_stored_ids(dumped, stored)

    assert _ids(lent) == _ids(stored)
    # Only the id moves: the dump is what is stored.
    assert [m.model_copy(update={"id": "x"}) for m in lent] == [
        m.model_copy(update={"id": "x"}) for m in dumped
    ]


def test_a_message_matches_within_its_own_kind_only() -> None:
    # A text answer and a reasoning message holding the same words are two
    # messages; neither is lent the other's id.
    dumped = [_text("fresh", "same words")]
    stored = [ReasoningMessage(id="reasoning", role="reasoning", content="same words")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh"]


def test_text_is_compared_without_its_newlines() -> None:
    # A row stored from a failed run's transcript holds an answer as it
    # streamed, where the dump joins the answer's parts with a newline.
    dumped = [_text("fresh", "first part\nsecond part")]
    stored = [_text("streamed", "first partsecond part")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["streamed"]


def test_repeated_answers_are_matched_in_order() -> None:
    dumped = [_text("fresh-1", "ok"), _text("fresh-2", "ok")]
    stored = [_text("first-ok", "ok"), _text("second-ok", "ok")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["first-ok", "second-ok"]


def test_a_stored_message_the_dump_lacks_is_passed_over() -> None:
    # The row holds a message the snapshot does not -- one a failed run stored
    # from its transcript, say -- and the walk moves past it.
    dumped = [_text("fresh-1", "kept"), _text("fresh-2", "ok")]
    stored = [_text("a", "kept"), _text("b", "only in the row"), _text("c", "ok")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["a", "c"]


def test_a_match_is_never_taken_from_behind_the_last_one() -> None:
    # The walk is ordered: once "later" has been matched, the earlier "ok" in
    # the row is behind it, and the dumped "ok" after "later" does not reach
    # back for it.
    dumped = [_text("fresh-1", "later"), _text("fresh-2", "ok")]
    stored = [_text("a", "ok"), _text("b", "later")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["b", "fresh-2"]


def test_a_message_no_stored_message_matches_keeps_its_id() -> None:
    dumped = [_text("fresh", "a different answer"), _tool("fresh-tool", "call-z")]
    stored = [_text("streamed", "the answer"), _tool("result", "call-a")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh", "fresh-tool"]


def test_a_row_id_is_lent_once() -> None:
    # A row that holds one id twice lends it to the first message only.
    dumped = [_text("fresh-1", "ok"), _text("fresh-2", "ok")]
    stored = [_text("twice", "ok"), _text("twice", "ok")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["twice", "fresh-2"]


def test_a_reserved_id_is_not_lent() -> None:
    # The new turn stored after these messages already carries the id.
    dumped = [_text("fresh", "ok")]
    stored = [_text("taken", "ok")]

    assert _ids(lend_stored_ids(dumped, stored, reserved={"taken"})) == ["fresh"]


def test_an_id_another_message_carries_is_not_lent() -> None:
    dumped = [_text("fresh", "ok"), UserMessage(id="carried", role="user", content="hi")]
    stored = [_text("carried", "ok")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh", "carried"]


def test_a_message_already_under_its_stored_id_is_left_as_it_is() -> None:
    message = _text("same", "ok")

    (kept,) = lend_stored_ids([message], [_text("same", "ok")])

    assert kept is message
