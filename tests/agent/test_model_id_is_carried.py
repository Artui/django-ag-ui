from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import ModelFallback
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from django_ag_ui.agent.message_model_id import MODEL_ID_FIELD
from django_ag_ui.agent.outcome_agui_adapter import OutcomeAGUIAdapter
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


def _streamed(frames: list[dict[str, Any]]) -> list[str | None]:
    return [
        (frame.get("metadata") or {}).get(MODEL_ID_FIELD)
        for frame in frames
        if frame["type"] == "TEXT_MESSAGE_END"
    ]


def _stored(store: _Store) -> list[str | None]:
    return [
        (m.get("metadata") or {}).get(MODEL_ID_FIELD)
        for m in _new(store.saved[0], after=1)
        if m["role"] == "assistant" and m.get("content")
    ]


async def test_an_answer_carries_its_model_id_streamed_and_stored() -> None:
    store, model = _Store(), _model(primary)

    frames = await _run(_session(Agent(model), store))

    assert _streamed(frames) == _stored(store) == [model.model_id]


async def test_a_fallback_answer_carries_the_fallback_model_id() -> None:
    store, down, up = _Store(), _model(broken), _model(spare)

    frames = await _run(_session(Agent(down, capabilities=[ModelFallback(up)]), store))

    assert up.model_id != down.model_id
    assert _streamed(frames) == _stored(store) == [up.model_id]


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

    assert _streamed(frames) == _stored(store) == [down.model_id, up.model_id]


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
    assert [m["metadata"][MODEL_ID_FIELD] for m in kept] == [first_model.model_id]
