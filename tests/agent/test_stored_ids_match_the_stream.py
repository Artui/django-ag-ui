from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from ag_ui.core import TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent
from django.test import RequestFactory, override_settings
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.policy.audit.null_audit_logger import NullAuditLogger
from pydantic_ai import Agent, BinaryContent, ToolReturn
from pydantic_ai.messages import (
    FilePart,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import function as function_module
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_agent_session import _approval_agent, _approval_run_input


class _Store:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    async def save(self, conversation: Any, *, request: Any) -> None:
        self.saved.append(conversation)

    async def load(self, thread_id: str, *, request: Any) -> Any:
        return None

    async def list_threads(self, *, request: Any) -> list[Any]:
        return []

    async def delete(self, thread_id: str, *, request: Any) -> bool:
        return False

    async def rename(self, thread_id: str, title: str, *, request: Any) -> bool:
        return False


USER_TURN: dict[str, Any] = {"id": "u1", "role": "user", "content": "hi"}


def _session(
    agent: Agent[Any, Any],
    store: _Store,
    *,
    messages: list[dict[str, Any]] | None = None,
    message_history: list[Any] | None = None,
) -> AgentSession:
    payload = {
        "threadId": "t1",
        "runId": "r1",
        "messages": messages if messages is not None else [USER_TURN],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
    }
    return AgentSession(
        agent,
        AGUIAdapter.build_run_input(json.dumps(payload).encode()),
        RequestFactory().post("/agent/"),
        deps=AgentDeps(user=None),
        audit_logger=NullAuditLogger(),
        config=build_ag_ui_config(),
        conversation_store=store,
        message_history=message_history,
    )


def _frames(chunks: list[str]) -> list[dict[str, Any]]:
    """The decoded ``data:`` frames of an SSE stream, in order."""
    return [
        json.loads(line[len("data: ") :])
        for line in "".join(chunks).splitlines()
        if line.startswith("data: ")
    ]


async def _run(session: AgentSession) -> list[dict[str, Any]]:
    return _frames([chunk async for chunk in session.stream()])


def _ids(frames: list[dict[str, Any]], kind: str) -> list[str]:
    return [frame["messageId"] for frame in frames if frame["type"] == kind]


def _new(conversation: Any, *, after: int) -> list[dict[str, Any]]:
    """The stored messages this run produced, past the ``after`` it was handed."""
    return conversation.messages[after:]


def _answering_agent() -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        yield "the answer"

    return Agent(FunctionModel(stream_function=stream_fn))


def _tool_round_agent() -> Agent[None, Any]:
    """Two calls in one tool-call-only response, then a text answer."""

    async def stream_fn(messages: list, info: Any) -> Any:
        if isinstance(messages[-1], ModelRequest) and any(
            part.part_kind == "tool-return" for part in messages[-1].parts
        ):
            yield "both looked up"
            return
        yield {0: DeltaToolCall(name="lookup", json_args='{"key": "a"}', tool_call_id="call-a")}
        yield {1: DeltaToolCall(name="lookup", json_args='{"key": "b"}', tool_call_id="call-b")}

    agent: Agent[None, Any] = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def lookup(key: str) -> str:
        return f"value of {key}"

    return agent


def _thinking_agent() -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        yield {0: DeltaThinkingPart(content="private pondering")}
        yield "ok"

    return Agent(FunctionModel(stream_function=stream_fn))


def _assert_unique(conversation: Any) -> None:
    ids = [message["id"] for message in conversation.messages]
    assert len(ids) == len(set(ids)), ids


async def test_a_text_answer_is_stored_under_the_id_it_streamed() -> None:
    store = _Store()

    frames = await _run(_session(_answering_agent(), store))

    (streamed,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    (answer,) = _new(conversation, after=1)
    assert answer["role"] == "assistant"
    assert answer["content"] == "the answer"
    assert answer["id"] == streamed


async def test_a_tool_round_is_stored_under_the_ids_it_streamed() -> None:
    store = _Store()

    frames = await _run(_session(_tool_round_agent(), store))

    starts = _ids(frames, "TEXT_MESSAGE_START")
    parents = {f["parentMessageId"] for f in frames if f["type"] == "TOOL_CALL_START"}
    results = {f["toolCallId"]: f["messageId"] for f in frames if f["type"] == "TOOL_CALL_RESULT"}
    # The empty message a tool-call-only response opens, then the answer's.
    calls_parent, answer_id = starts
    assert parents == {calls_parent}

    (conversation,) = store.saved
    calls, result_a, result_b, answer = _new(conversation, after=1)
    assert [call["id"] for call in calls["toolCalls"]] == ["call-a", "call-b"]
    assert calls["id"] == calls_parent
    assert (result_a["toolCallId"], result_a["id"]) == ("call-a", results["call-a"])
    assert (result_b["toolCallId"], result_b["id"]) == ("call-b", results["call-b"])
    assert answer["content"] == "both looked up"
    assert answer["id"] == answer_id
    _assert_unique(conversation)


async def test_reasoning_is_stored_under_the_id_it_streamed() -> None:
    store = _Store()

    frames = await _run(_session(_thinking_agent(), store))

    (reasoning_id,) = _ids(frames, "REASONING_START")
    (answer_id,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    reasoning, answer = _new(conversation, after=1)
    assert (reasoning["role"], reasoning["id"]) == ("reasoning", reasoning_id)
    assert (answer["role"], answer["id"]) == ("assistant", answer_id)


@override_settings(DJANGO_AG_UI={"FORWARD_REASONING": False})
async def test_withheld_reasoning_is_stored_under_an_id_of_its_own() -> None:
    # No reasoning id reached the client, so the stored copy keeps the id its dump minted.
    store = _Store()

    frames = await _run(_session(_thinking_agent(), store))

    assert _ids(frames, "REASONING_START") == []
    (answer_id,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    reasoning, answer = _new(conversation, after=1)
    assert reasoning["role"] == "reasoning"
    assert answer["id"] == answer_id
    _assert_unique(conversation)


async def test_the_next_turn_stores_the_earlier_answer_under_the_same_id() -> None:
    # A session that was not reloaded posts the ids it streamed.
    store = _Store()
    frames = await _run(_session(_answering_agent(), store))
    (streamed,) = _ids(frames, "TEXT_MESSAGE_START")
    as_the_client_holds_it = [
        USER_TURN,
        {"id": streamed, "role": "assistant", "content": "the answer"},
        {"id": "u2", "role": "user", "content": "and again"},
    ]

    await _run(_session(_answering_agent(), store, messages=as_the_client_holds_it))

    first, second = store.saved
    assert [m["id"] for m in first.messages] == [m["id"] for m in second.messages[:2]]


def _loaded_history() -> list[Any]:
    """A resumed run's snapshot, loaded from the client's messages as upstream loads them."""
    run_input = AGUIAdapter.build_run_input(
        json.dumps(
            {
                "threadId": "t1",
                "runId": "r0",
                "messages": [
                    {"id": "u0", "role": "user", "content": "older question"},
                    {"id": "a0", "role": "assistant", "content": "older answer"},
                ],
                "tools": [],
                "context": [],
                "state": None,
                "forwardedProps": None,
            }
        ).encode()
    )
    return AGUIAdapter.load_messages(run_input.messages)


async def test_a_resumed_run_leaves_the_prior_ids_to_the_dump() -> None:
    # Restamping covers only what this run produced; the snapshot prefix keeps the dump's ids.
    store = _Store()

    frames = await _run(_session(_answering_agent(), store, message_history=_loaded_history()))

    (streamed,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    prior, (posted, answer) = conversation.messages[:2], conversation.messages[2:]
    assert [m["content"] for m in prior] == ["older question", "older answer"]
    assert streamed not in [m["id"] for m in prior]
    assert (posted["id"], answer["id"]) == ("u1", streamed)
    _assert_unique(conversation)


async def test_a_resumed_run_keeps_the_ids_upstream_restores() -> None:
    # pydantic-ai restores a loaded message's id on dump from 2.48; below that no prior id is left.
    restores = [m.id for m in AGUIAdapter.dump_messages(_loaded_history())] == ["u0", "a0"]
    if not restores:
        pytest.skip("this pydantic-ai does not restore a loaded message's id on dump")
    store = _Store()

    frames = await _run(_session(_answering_agent(), store, message_history=_loaded_history()))

    (streamed,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    assert [m["id"] for m in conversation.messages] == ["u0", "a0", "u1", streamed]


async def test_a_resumed_run_from_a_bare_snapshot_still_streams_its_new_ids() -> None:
    # What this run produced is stored the way it streamed, whatever the snapshot carries.
    store = _Store()
    history = [
        ModelRequest(parts=[UserPromptPart(content="older question")]),
        ModelResponse(parts=[TextPart(content="older answer")]),
    ]

    frames = await _run(_session(_answering_agent(), store, message_history=history))

    (streamed,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    assert conversation.messages[-1]["id"] == streamed
    _assert_unique(conversation)


async def test_a_snapshots_generated_turns_are_named_afresh_on_every_save() -> None:
    # A snapshot with no recorded ids and no row gets fresh ids.
    store = _Store()

    def history() -> list[Any]:
        return [
            ModelRequest(parts=[UserPromptPart(content="older question")]),
            ModelResponse(parts=[TextPart(content="older answer")]),
        ]

    await _run(_session(_answering_agent(), store, message_history=history()))
    await _run(_session(_answering_agent(), store, message_history=history()))

    first, second = store.saved
    assert first.messages[1]["content"] == second.messages[1]["content"] == "older answer"
    assert first.messages[1]["id"] != second.messages[1]["id"]


async def test_an_approved_call_resumes_under_the_ids_it_streamed() -> None:
    # The approved call's result and the answer after it are new; no id names two of them.
    store = _Store()
    resume = [{"interruptId": "int-call-1", "status": "resolved", "payload": {"approved": True}}]
    session = AgentSession(
        _approval_agent([]),
        _approval_run_input(resume=resume),
        RequestFactory().post("/agent/"),
        deps=AgentDeps(user=None),
        audit_logger=NullAuditLogger(),
        config=build_ag_ui_config(),
        conversation_store=store,
    )

    frames = await _run(session)

    (result_id,) = _ids(frames, "TOOL_CALL_RESULT")
    (answer_id,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    posted, (result, answer) = conversation.messages[:2], conversation.messages[2:]
    assert [m["id"] for m in posted] == ["m1", "a1"]
    assert (result["role"], result["id"]) == ("tool", result_id)
    assert (answer["role"], answer["id"]) == ("assistant", answer_id)
    _assert_unique(conversation)


# --- a dump that splits and joins differently from the stream ---------------------
#
# Where the dump and the stream disagree about how many text messages a run held,
# two disagreements in opposite directions leave the counts equal and the
# positions shifted. The answer there is a fresh id, never a neighbour's: a rating
# keyed by one answer's id must not land on another after a reload.


def _streamed_texts(frames: list[dict[str, Any]]) -> dict[str, str]:
    """Each streamed text message's whole text, by its ``messageId``."""
    texts: dict[str, str] = {}
    for frame in frames:
        if frame["type"] == "TEXT_MESSAGE_START":
            texts.setdefault(frame["messageId"], "")
        elif frame["type"] == "TEXT_MESSAGE_CONTENT":
            texts[frame["messageId"]] = texts.get(frame["messageId"], "") + frame["delta"]
    return texts


def _borrowed(frames: list[dict[str, Any]], stored: list[dict[str, Any]]) -> list[str]:
    """Each stored assistant message under a streamed id whose text it does not hold."""
    streamed = _streamed_texts(frames)
    return [
        f"{message['id']}: stored {message.get('content')!r}, streamed {streamed[message['id']]!r}"
        for message in stored
        if message["role"] == "assistant"
        and message["id"] in streamed
        and (message.get("content") or "") != streamed[message["id"]]
    ]


def _after_a_tool_return(messages: list) -> bool:
    return isinstance(messages[-1], ModelRequest) and any(
        part.part_kind == "tool-return" for part in messages[-1].parts
    )


async def test_a_split_answer_and_a_tool_spoken_text_borrow_no_id() -> None:
    # Text, thinking, then a call: the dump flushes the text alone, unannounced.
    async def stream_fn(messages: list, info: Any) -> Any:
        if _after_a_tool_return(messages):
            yield "final"
            return
        yield "A"
        yield {0: DeltaThinkingPart(content="hm")}
        yield {1: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="c1")}

    agent: Agent[None, Any] = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def lookup() -> ToolReturn:
        return ToolReturn(
            return_value="ok",
            metadata=[
                TextMessageStartEvent(message_id="tool-said"),
                TextMessageContentEvent(message_id="tool-said", delta="from the tool"),
                TextMessageEndEvent(message_id="tool-said"),
            ],
        )

    store = _Store()

    frames = await _run(_session(agent, store))

    (conversation,) = store.saved
    stored = _new(conversation, after=1)
    assert [m["role"] for m in stored] == [
        "assistant",
        "reasoning",
        "assistant",
        "tool",
        "assistant",
    ]
    assert _borrowed(frames, stored) == []
    _assert_unique(conversation)


async def test_a_split_answer_and_a_joined_answer_borrow_no_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same split, joined by a cancel: the dump drops the file part and holds one merged text.
    original = function_module.FunctionStreamedResponse._get_event_iterator

    async def _one(item: Any) -> Any:
        yield item

    async def _iterator(self: Any) -> Any:
        async for item in self._iter:
            if isinstance(item, TextPart | FilePart):
                yield self._parts_manager.handle_part(vendor_part_id=None, part=item)
            else:
                self._iter = _one(item)
                async for event in original(self):
                    yield event

    monkeypatch.setattr(function_module.FunctionStreamedResponse, "_get_event_iterator", _iterator)

    async def stream_fn(messages: list, info: Any) -> Any:
        if _after_a_tool_return(messages):
            yield TextPart(content="B")
            yield FilePart(content=BinaryContent(data=b"png", media_type="image/png"))
            yield TextPart(content="C")
            return
        yield TextPart(content="A")
        yield {0: DeltaThinkingPart(content="hm")}
        yield {1: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="c1")}

    agent: Agent[None, Any] = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def lookup() -> str:
        return "ok"

    store = _Store()

    frames = await _run(_session(agent, store))

    (conversation,) = store.saved
    stored = _new(conversation, after=1)
    assert [m.get("content") for m in stored if m["role"] == "assistant"] == ["A", None, "B\nC"]
    assert _borrowed(frames, stored) == []
    _assert_unique(conversation)


# --- the other two exits already stored the streamed ids --------------------------


async def test_a_failed_run_stores_the_ids_it_streamed() -> None:
    async def stream_fn(messages: list, info: Any) -> Any:
        yield {0: DeltaToolCall(name="boom", json_args="{}", tool_call_id="call-1")}

    agent: Agent[None, Any] = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def boom() -> str:
        raise RuntimeError("kaboom")

    store = _Store()

    frames = await _run(_session(agent, store))

    assert frames[-1]["type"] == "RUN_ERROR"
    (parent,) = {f["parentMessageId"] for f in frames if f["type"] == "TOOL_CALL_START"}
    (result_id,) = _ids(frames, "TOOL_CALL_RESULT")
    (conversation,) = store.saved
    calls, result = _new(conversation, after=1)
    assert calls["id"] == parent
    assert result["id"] == result_id


async def test_a_cancelled_run_stores_the_id_it_streamed() -> None:
    async def stream_fn(messages: list, info: Any) -> Any:
        yield "partial answer"
        await asyncio.Event().wait()

    store = _Store()
    stream = _session(Agent(FunctionModel(stream_function=stream_fn)), store).stream()
    chunks: list[str] = []
    async for chunk in stream:
        chunks.append(chunk)
        if "partial answer" in chunk:
            break
    await stream.aclose()

    (streamed,) = _ids(_frames(chunks), "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    (partial,) = _new(conversation, after=1)
    assert partial["id"] == streamed
