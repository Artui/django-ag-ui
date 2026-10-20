"""A stored thread names each message by the id its stream announced.

A client keys whatever it attaches to a message -- a rating, a quote, a scroll
anchor -- by the id it saw on ``TEXT_MESSAGE_START`` or ``TOOL_CALL_RESULT``. If
the stored copy names the same message differently, everything keyed that way is
orphaned the moment the thread is reloaded from the server, and on the next turn
of a session that was *not* reloaded the client posts its own ids back, so the
same message is stored under one id and then another.

Every assertion here reads the payload the store was handed -- the jsonable,
camelCase dicts -- against the ``messageId`` fields of the SSE frames, rather than
round-tripping either through pydantic-ai's types.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from django.test import RequestFactory, override_settings
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config


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
    """Two calls in one tool-call-only response, then a text answer.

    Tool-call-only, so the stream opens and closes an empty text message only to
    give the calls a parent; two calls, so one ``ModelRequest`` dumps as two
    ``ToolMessage``\\s and the per-message rule upstream applies to a loaded
    message cannot reach both.
    """

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
    # No reasoning id reached the client, so there is none to keep: the stored
    # copy keeps the one its dump minted, and the answer beside it still keeps
    # the id the client did see.
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
    # A session that was not reloaded posts the ids it streamed. Before the
    # completed run kept them, the first save named the answer one way and the
    # second save the other.
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


async def test_a_resumed_run_keeps_the_prior_ids_and_streams_the_new_ones() -> None:
    # The snapshot a resumed run is seeded with carries the UI id of each message
    # it was loaded from, the way ``load_messages`` leaves it. Those are restored
    # by the dump; the restamping touches only what this run produced.
    store = _Store()
    history = AGUIAdapter.load_messages(
        AGUIAdapter.build_run_input(
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
        ).messages
    )

    frames = await _run(_session(_answering_agent(), store, message_history=history))

    (streamed,) = _ids(frames, "TEXT_MESSAGE_START")
    (conversation,) = store.saved
    assert [m["id"] for m in conversation.messages] == ["u0", "a0", "u1", streamed]


async def test_a_resumed_run_from_a_bare_snapshot_still_streams_its_new_ids() -> None:
    # A snapshot carrying no UI ids is dumped under fresh ones, as before; what
    # this run produced is still stored the way it streamed.
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
