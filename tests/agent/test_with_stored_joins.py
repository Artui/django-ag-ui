from __future__ import annotations

from typing import Any

from ag_ui.core import AssistantMessage, FunctionCall, ToolCall, UserMessage
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.with_stored_joins import with_stored_joins
from tests.agent.test_agent_session import _events, _RecordingStore, _run_input, _session
from tests.agent.test_split_joined_texts import FILE


def _text(message_id: str, content: str) -> AssistantMessage:
    return AssistantMessage(id=message_id, role="assistant", content=content)


def _user(message_id: str, content: str) -> UserMessage:
    return UserMessage(id=message_id, role="user", content=content)


async def test_a_resumed_run_keeps_an_answer_stored_joined_under_its_id() -> None:
    history = [
        ModelRequest.user_text_prompt("hi"),
        ModelResponse(parts=[TextPart(content="B"), FILE, TextPart(content="C")]),
    ]
    stored = [_user("u1", "hi"), _text("X", "B\nC")]
    store = _RecordingStore()
    session = _session(
        Agent(TestModel(custom_output_text="next")),
        _run_input([{"id": "u2", "role": "user", "content": "more?"}]),
        conversation_store=store,
        message_history=history,
        source_messages=stored,
    )

    await _events(session)

    (conversation,) = store.saved
    kept = [(m["id"], m.get("content")) for m in conversation.messages[:2]]
    assert kept == [("u1", "hi"), ("X", "B\nC")]


def test_pieces_spelling_a_joined_row_become_that_row() -> None:
    row = _text("X", "A\nB\nC")
    dumped: list[Any] = [_text("p1", "A"), _text("p2", "B"), _text("p3", "C"), _user("u", "q")]

    assert with_stored_joins(dumped, [row]) == [row, dumped[-1]]


def test_a_piece_some_row_holds_alone_is_never_joined() -> None:
    dumped: list[Any] = [_text("p1", "B"), _text("p2", "C")]

    assert with_stored_joins(dumped, [_text("X", "B\nC"), _text("Y", "C")]) == dumped


def test_pieces_that_spell_no_row_stay_apart() -> None:
    dumped: list[Any] = [_text("p1", "B"), _text("p2", "C"), _text("p3", "D")]

    assert with_stored_joins(dumped, [_text("X", "B\nD")]) == dumped


def test_a_joined_row_is_taken_once() -> None:
    row = _text("X", "B\nC")
    dumped: list[Any] = [_text("p1", "B"), _text("p2", "C"), _text("p3", "B"), _text("p4", "C")]

    assert with_stored_joins(dumped, [row]) == [row, *dumped[2:]]


def test_a_calls_message_ends_a_run_of_pieces() -> None:
    call = ToolCall(id="c1", type="function", function=FunctionCall(name="t", arguments="{}"))
    calls = AssistantMessage(id="p2", role="assistant", content="C", tool_calls=[call])
    dumped: list[Any] = [_text("p1", "B"), calls]

    assert with_stored_joins(dumped, [_text("X", "B\nC")]) == dumped


def test_only_a_stored_answer_is_taken_for_the_pieces() -> None:
    dumped: list[Any] = [_text("p1", "B"), _text("p2", "C")]

    assert with_stored_joins(dumped, [_user("X", "B\nC")]) == dumped
