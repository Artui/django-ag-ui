from __future__ import annotations

from ag_ui.core import (
    ActivityMessage,
    AssistantMessage,
    FunctionCall,
    Message,
    ReasoningMessage,
    SystemMessage,
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
    # A text answer and a reasoning message with the same words are two messages, lent no ids.
    dumped = [_text("fresh", "same words")]
    stored = [ReasoningMessage(id="reasoning", role="reasoning", content="same words")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh"]


def test_text_is_compared_without_its_newlines() -> None:
    # A failed run's row holds the streamed answer, where the dump joins parts with a newline.
    dumped = [_text("fresh", "first part\nsecond part")]
    stored = [_text("streamed", "first partsecond part")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["streamed"]


def test_repeated_answers_are_matched_in_order() -> None:
    dumped = [_text("fresh-1", "ok"), _text("fresh-2", "ok")]
    stored = [_text("first-ok", "ok"), _text("second-ok", "ok")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["first-ok", "second-ok"]


def test_a_stored_message_the_dump_lacks_is_passed_over() -> None:
    # A row message the snapshot lacks, from a failed run, is walked past.
    dumped = [_text("fresh-1", "kept"), _text("fresh-2", "ok")]
    stored = [_text("a", "kept"), _text("b", "only in the row"), _text("c", "ok")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["a", "c"]


def test_a_match_is_never_taken_from_behind_the_last_one() -> None:
    # The walk is ordered: once later is matched, an earlier ok behind it is not reached back for.
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


def test_a_calling_message_takes_the_id_of_the_one_with_its_calls() -> None:
    # Matched by its calls, not by being a message with calls: the first has other ones.
    lent = lend_stored_ids(
        [_calls("fresh", "call-b")], [_calls("first", "call-a"), _calls("second", "call-b")]
    )

    assert _ids(lent) == ["second"]


def _activity(message_id: str, activity_type: str, **content: object) -> ActivityMessage:
    return ActivityMessage(id=message_id, activity_type=activity_type, content=content)


def test_an_activity_takes_the_id_of_the_stored_one_with_its_content() -> None:
    dumped = [
        _activity("fresh-1", "pydantic_ai_compaction", content="first"),
        _activity("fresh-2", "pydantic_ai_compaction", content="second"),
    ]
    stored = [
        _activity("streamed-1", "pydantic_ai_compaction", content="first"),
        _activity("streamed-2", "pydantic_ai_compaction", content="second"),
        _activity("later", "pydantic_ai_compaction", content="third"),
    ]

    assert _ids(lend_stored_ids(dumped, stored)) == ["streamed-1", "streamed-2"]


def test_an_activity_whose_content_differs_keeps_its_id() -> None:
    dumped = [_activity("fresh", "pydantic_ai_compaction", content="first")]
    stored = [_activity("streamed", "pydantic_ai_compaction", content="rewritten")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh"]


def test_an_activity_matches_within_its_own_type_only() -> None:
    # The same payload under another activity type is another activity.
    dumped = [_activity("fresh", "pydantic_ai_compaction", content="same")]
    stored = [_activity("other", "compaction", content="same")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh"]


def test_an_activity_is_not_lent_an_id_from_another_position() -> None:
    # Pairing is by position, never by looking ahead.
    dumped = [_activity("fresh", "pydantic_ai_compaction", content="x")]
    stored = [
        _activity("other", "pydantic_ai_compaction", content="y"),
        _activity("later", "pydantic_ai_compaction", content="x"),
    ]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh"]


def test_an_activity_is_not_lent_an_id_when_the_row_holds_fewer() -> None:
    # Two dumped, one stored: nothing says which of the two the stored one was.
    dumped = [
        _activity("fresh-1", "pydantic_ai_compaction", content="f1"),
        _activity("fresh-2", "pydantic_ai_compaction", content="f2"),
    ]
    stored = [_activity("only", "pydantic_ai_compaction", content="f1")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh-1", "fresh-2"]


def test_a_type_whose_pairing_fails_keeps_every_dumped_id() -> None:
    # The first pair agrees and the second does not: the type keeps its ids, others judged alone.
    dumped = [
        _activity("fresh-1", "pydantic_ai_compaction", content="a"),
        _activity("fresh-2", "pydantic_ai_compaction", content="b"),
        _activity("fresh-3", "chart", rows=1),
    ]
    stored = [
        _activity("s-1", "pydantic_ai_compaction", content="a"),
        _activity("s-2", "pydantic_ai_compaction", content="changed"),
        _activity("s-3", "chart", rows=1),
    ]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh-1", "fresh-2", "s-3"]


def test_an_activity_is_matched_whole_with_its_canonical_system() -> None:
    # The row holds what the completed run stored, so contents compare as they are.
    both = {"provider_details": {"encrypted_content": "e"}, "canonical_system": "openai"}
    dumped = [_activity("fresh", "pydantic_ai_compaction", **both)]
    stored = [_activity("streamed", "pydantic_ai_compaction", **both)]
    assert _ids(lend_stored_ids(dumped, stored)) == ["streamed"]

    bare = [
        _activity("streamed", "pydantic_ai_compaction", provider_details=both["provider_details"])
    ]
    assert _ids(lend_stored_ids(dumped, bare)) == ["fresh"]


def test_an_edited_resubmit_keeps_the_streamed_id_of_the_compaction_it_kept() -> None:
    # r1 and r2 each compacted; r3 edits the second user message, so the row holds both.
    dumped = [
        UserMessage(id="u1", role="user", content="first"),
        _activity("fresh-1", "pydantic_ai_compaction", content="summary 1"),
        _text("fresh-a", "done"),
    ]
    stored = [
        UserMessage(id="u1", role="user", content="first"),
        _activity("streamed-1", "pydantic_ai_compaction", content="summary 1"),
        _text("streamed-a", "done"),
        UserMessage(id="u2", role="user", content="second"),
        _activity("streamed-2", "pydantic_ai_compaction", content="summary 2"),
        _text("streamed-b", "done"),
    ]

    assert _ids(lend_stored_ids(dumped, stored)) == ["u1", "streamed-1", "streamed-a"]


def _user(message_id: str, content: str) -> UserMessage:
    return UserMessage(id=message_id, role="user", content=content)


def _summary(message_id: str) -> ActivityMessage:
    return _activity(message_id, "pydantic_ai_compaction", content="summary")


def test_a_kept_prompt_merged_with_a_summary_takes_its_id_back_from_the_summary() -> None:
    dumped = [_user("fresh", "first"), _summary("u0")]
    stored = [_user("u0", "first"), _summary("streamed")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["u0", "streamed"]


def test_a_kept_prompt_whose_id_a_three_way_merge_dropped_takes_it_back() -> None:
    dumped = [_user("fresh-1", "first"), _summary("fresh-2"), _user("u5", "fifth")]
    stored = [_user("u0", "first"), _summary("streamed"), _user("u5", "fifth")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["u0", "streamed", "u5"]


def test_a_prompt_already_under_a_stored_id_keeps_it_and_its_place() -> None:
    dumped = [_user("u2", "yes"), _user("fresh", "yes")]
    stored = [_user("u1", "yes"), _user("u2", "yes")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["u2", "fresh"]


def test_a_summary_that_cannot_take_its_stored_id_keeps_the_one_it_has() -> None:
    dumped = [_user("fresh", "first"), _summary("u0"), _text("streamed", "ok")]
    stored = [_user("u0", "first"), _summary("streamed")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh", "u0", "streamed"]


def test_a_summary_whose_stored_id_is_reserved_keeps_the_one_it_has() -> None:
    dumped = [_user("fresh", "first"), _summary("u0")]
    stored = [_user("u0", "first"), _summary("streamed")]

    assert _ids(lend_stored_ids(dumped, stored, reserved={"streamed"})) == ["fresh", "u0"]


def test_a_system_message_is_never_lent_an_id() -> None:
    dumped = [SystemMessage(id="fresh", role="system", content="rules")]
    stored = [SystemMessage(id="stored", role="system", content="rules")]

    assert _ids(lend_stored_ids(dumped, stored)) == ["fresh"]
