from __future__ import annotations

from ag_ui.core import AssistantMessage, Message, UserMessage

from django_ag_ui.agent.full_transcript import full_transcript
from django_ag_ui.agent.lend_stored_ids import lend_stored_ids


def _user(message_id: str, content: str) -> UserMessage:
    return UserMessage(id=message_id, role="user", content=content)


def _answer(message_id: str, content: str = "done") -> AssistantMessage:
    return AssistantMessage(id=message_id, role="assistant", content=content)


def _ids(messages: list[Message]) -> list[str]:
    return [message.id for message in messages]


_ROW: list[Message] = [
    _user("u1", "one"),
    _answer("a1"),
    _user("u2", "two"),
    _answer("a2"),
    _user("u3", "three"),
    _answer("a3"),
]


def _resumed(kept: list[Message], row: list[Message] = _ROW) -> list[Message]:
    return full_transcript(lend_stored_ids(kept, row), row)


def test_a_compacted_history_keeps_every_turn_of_the_row() -> None:
    kept: list[Message] = [_user("u3", "three"), _answer("fresh")]

    assert _ids(_resumed(kept)) == _ids(_ROW)


def test_identical_answers_bind_after_the_prompt_the_dump_keeps() -> None:
    # The kept answer would bind to the compacted-out a1 without the cursor moving past u3.
    kept: list[Message] = [_user("u1", "one"), _user("u3", "three"), _answer("fresh")]

    assert _ids(_resumed(kept)) == _ids(_ROW)


def test_with_no_row_the_dump_is_the_transcript() -> None:
    kept: list[Message] = [_user("u3", "three"), _answer("fresh")]

    assert _ids(_resumed(kept, [])) == ["u3", "fresh"]


def test_a_diverged_row_lends_nothing_and_keeps_the_dump() -> None:
    row: list[Message] = [_user("x1", "other"), _answer("x2", "elsewhere")]
    kept: list[Message] = [_user("u3", "three"), _answer("fresh")]

    assert _ids(_resumed(kept, row)) == ["u3", "fresh"]


def test_a_resume_of_an_older_run_drops_the_row_after_it() -> None:
    kept: list[Message] = [_user("u2", "two"), _answer("fresh")]

    assert _ids(_resumed(kept)) == ["u1", "a1", "u2", "a2"]


def test_an_edit_cut_ends_the_prefix_before_the_edited_prompt() -> None:
    kept: list[Message] = [_user("u1", "one"), _answer("fresh")]

    assert _ids(_resumed(kept)) == ["u1", "a1"]


def test_a_failed_source_runs_tail_follows_the_row() -> None:
    row: list[Message] = [*_ROW, _user("u4", "four")]
    kept: list[Message] = [_user("u4", "four"), _answer("partial", "half an answer")]

    assert _ids(_resumed(kept, row)) == [*_ids(row), "partial"]


def test_a_tail_message_the_row_already_holds_is_not_stored_twice() -> None:
    kept: list[Message] = [_user("u3", "three"), _answer("a1")]

    assert _ids(full_transcript(kept, _ROW)) == _ids(_ROW[:5])
