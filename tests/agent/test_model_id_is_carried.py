from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.capabilities import ModelFallback
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import FilePart, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from django_ag_ui.agent.message_model_id import MODEL_FALLBACK_FIELD, MODEL_ID_FIELD
from django_ag_ui.agent.outcome_agui_adapter import OutcomeAGUIAdapter
from tests.agent.test_citations_are_carried import _whole_parts  # noqa: F401
from tests.agent.test_stored_ids_match_the_stream import (
    _new,
    _run,
    _session,
    _Store,
)


async def primary(messages: list, info: Any) -> Any:
    yield "from the primary"


async def broken(messages: list, info: Any) -> Any:
    raise ModelAPIError(model_name="broken", message="down")
    yield ""  # pragma: no cover


async def spare(messages: list, info: Any) -> Any:
    yield "from the spare"


def _model(fn: Any) -> FunctionModel:
    return FunctionModel(stream_function=fn)


def _marks(metadata: dict[str, Any] | None) -> tuple[str | None, Any]:
    return (metadata or {}).get(MODEL_ID_FIELD), (metadata or {}).get(MODEL_FALLBACK_FIELD)


def _streamed(frames: list[dict[str, Any]]) -> list[tuple[str | None, Any]]:
    return [
        _marks(frame.get("metadata")) for frame in frames if frame["type"] == "TEXT_MESSAGE_END"
    ]


def _stored(store: _Store, at: int = 0) -> list[tuple[str | None, Any]]:
    return [
        _marks(m.get("metadata"))
        for m in _new(store.saved[at], after=1)
        if m["role"] == "assistant" and m.get("content")
    ]


async def test_an_answer_carries_its_model_id_streamed_and_stored() -> None:
    store, model = _Store(), _model(primary)

    frames = await _run(_session(Agent(model), store))

    assert _streamed(frames) == _stored(store) == [(model.model_id, None)]


async def test_a_fallback_answer_carries_the_fallback_model_id() -> None:
    store, down, up = _Store(), _model(broken), _model(spare)

    frames = await _run(_session(Agent(down, capabilities=[ModelFallback(up)]), store))

    assert up.model_id != down.model_id
    assert _streamed(frames) == _stored(store) == [(up.model_id, True)]


async def test_each_response_carries_the_model_that_gave_it() -> None:
    calls = {"n": 0}

    async def first_up_then_down(messages: list, info: Any) -> Any:
        calls["n"] += 1
        if calls["n"] > 1:
            raise ModelAPIError(model_name="broken", message="down")
        yield "looking"
        yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="c1")}

    store, down, up = _Store(), _model(first_up_then_down), _model(spare)
    agent: Agent[None, Any] = Agent(down, capabilities=[ModelFallback(up)])

    @agent.tool_plain
    def lookup() -> str:
        return "ok"

    frames = await _run(_session(agent, store))

    assert _streamed(frames) == _stored(store) == [(down.model_id, None), (up.model_id, True)]


def test_a_response_without_a_model_id_stores_no_key() -> None:
    history = [
        ModelRequest.user_text_prompt("hi"),
        ModelResponse(parts=[TextPart(content="old answer")]),
    ]

    (_, answer) = OutcomeAGUIAdapter.dump_messages(history)

    assert MODEL_ID_FIELD not in (answer.metadata or {})


def test_replay_keeps_the_model_id() -> None:
    history = [
        ModelRequest.user_text_prompt("hi"),
        ModelResponse(parts=[TextPart(content="answer")], model_id="openai:gpt-5"),
    ]

    (_, answer) = OutcomeAGUIAdapter.dump_messages(history)

    assert answer.metadata[MODEL_ID_FIELD] == "openai:gpt-5"
    assert MODEL_FALLBACK_FIELD not in answer.metadata


def test_replay_keeps_the_fallback_mark_the_response_recorded() -> None:
    answer = ModelResponse(
        parts=[TextPart(content="answer")],
        model_id="anthropic:claude",
        metadata={MODEL_FALLBACK_FIELD: True},
    )

    (_, dumped) = OutcomeAGUIAdapter.dump_messages([ModelRequest.user_text_prompt("hi"), answer])

    assert _marks(dumped.metadata) == ("anthropic:claude", True)


async def test_every_piece_of_a_split_fallback_answer_carries_the_marks() -> None:
    async def split(messages: list, info: Any) -> Any:
        yield TextPart(content="before")
        yield FilePart(content=BinaryContent(data=b"x", media_type="image/png"))
        yield TextPart(content="after")

    store, down, up = _Store(), _model(broken), _model(split)

    frames = await _run(_session(Agent(down, capabilities=[ModelFallback(up)]), store))

    assert _streamed(frames) == [(up.model_id, True), (up.model_id, True)]
    assert set(_stored(store)) == {(up.model_id, True)}


def test_a_tool_call_only_message_carries_no_model_id() -> None:
    call = ToolCallPart(tool_name="lookup", args={}, tool_call_id="c1")
    history = [
        ModelRequest.user_text_prompt("hi"),
        ModelResponse(parts=[call], model_id="openai:gpt-5"),
    ]

    (_, calling) = OutcomeAGUIAdapter.dump_messages(history)

    assert MODEL_ID_FIELD not in (calling.metadata or {})


async def test_a_posted_back_answer_keeps_its_model_id_in_the_next_save() -> None:
    store, first_model = _Store(), _model(primary)
    await _run(_session(Agent(first_model), store))
    first = store.saved[0].messages

    more = {"id": "u2", "role": "user", "content": "more?"}
    await _run(_session(Agent(_model(spare)), store, messages=[*first, more]))

    kept = [m for m in store.saved[1].messages if m.get("content") == "from the primary"]
    assert [_marks(m["metadata"]) for m in kept] == [(first_model.model_id, None)]


async def test_a_posted_back_fallback_answer_keeps_its_mark_in_the_next_save() -> None:
    store, up = _Store(), _model(spare)
    await _run(_session(Agent(_model(broken), capabilities=[ModelFallback(up)]), store))
    more = {"id": "u2", "role": "user", "content": "more?"}

    await _run(_session(Agent(_model(primary)), store, messages=[*store.saved[0].messages, more]))

    kept = [m for m in store.saved[1].messages if m.get("content") == "from the spare"]
    assert [_marks(m["metadata"]) for m in kept] == [(up.model_id, True)]
