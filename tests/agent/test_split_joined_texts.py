from __future__ import annotations

from typing import Any

from ag_ui.core import AssistantMessage, UserMessage
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.messages import FilePart, ModelRequest, ModelResponse, TextPart, ThinkingPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.continued_history import continued_history
from django_ag_ui.agent.lend_stored_ids import lend_stored_ids
from django_ag_ui.agent.message_model_id import MODEL_ID_FIELD
from django_ag_ui.agent.outcome_agui_adapter import OutcomeAGUIAdapter
from django_ag_ui.agent.split_joined_texts import split_joined_texts
from tests.agent.test_citations_are_carried import _whole_parts  # noqa: F401
from tests.agent.test_stored_ids_match_the_stream import (
    _after_a_tool_return,
    _assert_unique,
    _ids,
    _new,
    _run,
    _session,
    _Store,
)

FILE = FilePart(content=BinaryContent(data=b"png", media_type="image/png"))


def _agent(*first: Any) -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        if _after_a_tool_return(messages):
            yield TextPart(content="done")
            return
        for item in first:
            yield item

    agent: Agent[None, Any] = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def lookup() -> str:
        return "ok"

    return agent


def _texts(stored: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    return [(m["id"], m.get("content")) for m in stored if m["role"] == "assistant"]


def _assistant(message_id: str, content: str) -> AssistantMessage:
    return AssistantMessage(id=message_id, role="assistant", content=content)


async def test_each_piece_of_a_file_split_answer_is_stored_under_its_streamed_id() -> None:
    store = _Store()

    frames = await _run(_session(_agent(TextPart(content="B"), FILE, TextPart(content="C")), store))

    stored = _new(store.saved[0], after=1)
    assert _texts(stored) == list(zip(_ids(frames, "TEXT_MESSAGE_START"), ["B", "C"], strict=True))
    assert all(MODEL_ID_FIELD in m["metadata"] for m in stored)
    _assert_unique(store.saved[0])


async def test_a_call_after_a_file_split_rides_on_the_piece_it_streamed_under() -> None:
    store = _Store()
    call = {2: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="c1")}

    frames = await _run(
        _session(_agent(TextPart(content="B"), FILE, TextPart(content="C"), call), store)
    )

    b_id, c_id, done_id = _ids(frames, "TEXT_MESSAGE_START")
    (parent,) = {f["parentMessageId"] for f in frames if f["type"] == "TOOL_CALL_START"}
    first, calling, result, done = _new(store.saved[0], after=1)
    assert (first["id"], first["content"]) == (b_id, "B")
    assert (calling["id"], calling["content"], parent) == (c_id, "C", c_id)
    assert [c["id"] for c in calling["toolCalls"]] == ["c1"]
    assert (result["toolCallId"], done["id"], done["content"]) == ("c1", done_id, "done")


async def test_a_posted_back_split_answer_keeps_both_pieces_in_the_next_save() -> None:
    store = _Store()
    await _run(_session(_agent(TextPart(content="B"), FILE, TextPart(content="C")), store))
    first = store.saved[0].messages

    await _run(
        _session(
            _agent(TextPart(content="again")),
            store,
            messages=[*first, {"id": "u2", "role": "user", "content": "more?"}],
        )
    )

    assert store.saved[1].messages[: len(first)] == first


def test_server_held_history_dumps_the_pieces_under_the_ids_stored() -> None:
    response = ModelResponse(parts=[TextPart(content="B"), FILE, TextPart(content="C")])
    history = [ModelRequest.user_text_prompt("hi"), response]
    stored = [
        UserMessage(id="u1", role="user", content="hi"),
        _assistant("m0", "B"),
        _assistant("m1", "C"),
    ]

    kept, _ = continued_history(history, [*stored, UserMessage(id="u2", role="user", content="?")])
    lent = lend_stored_ids(OutcomeAGUIAdapter.dump_messages(kept), stored)

    assert [(m.id, m.content) for m in lent[1:]] == [("m0", "B"), ("m1", "C")]


def test_adjacent_texts_and_a_newline_inside_one_stay_one_message() -> None:
    response = ModelResponse(parts=[TextPart(content="B\n"), TextPart(content="C"), FILE])
    dumped = AGUIAdapter._dump_response_parts(response)

    assert split_joined_texts(response, dumped) == dumped


def test_a_text_that_starts_with_a_newline_after_a_file_is_cut_after_the_join() -> None:
    response = ModelResponse(parts=[TextPart(content="B"), FILE, TextPart(content="\nC")])

    (first, second) = split_joined_texts(response, AGUIAdapter._dump_response_parts(response))

    assert (first.content, second.content) == ("B", "\nC")


def test_three_pieces_split_where_each_streamed() -> None:
    parts = [TextPart(content="A"), FILE, TextPart(content="B"), FILE, TextPart(content="C")]
    response = ModelResponse(parts=parts)
    dumped = AGUIAdapter._dump_response_parts(response)

    split = split_joined_texts(response, dumped)

    assert [m.content for m in split] == ["A", "B", "C"]
    assert split[-1].id == dumped[-1].id
    assert len({m.id for m in split}) == 3


def test_reasoning_between_the_texts_is_passed_over() -> None:
    parts = [ThinkingPart(content="hm"), TextPart(content="B"), FILE, TextPart(content="C")]
    response = ModelResponse(parts=parts)

    split = split_joined_texts(response, AGUIAdapter._dump_response_parts(response))

    assert [(m.role, m.content) for m in split] == [
        ("reasoning", "hm"),
        ("assistant", "B"),
        ("assistant", "C"),
    ]


def test_a_dump_the_parts_do_not_spell_is_left_whole() -> None:
    response = ModelResponse(parts=[TextPart(content="B"), FILE, TextPart(content="C")])
    dumped: list[Any] = [_assistant("m", "B\nC and more")]

    assert split_joined_texts(response, dumped) == dumped
