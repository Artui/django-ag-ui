"""A resumed or forked run, end to end: what the model sees and what the row keeps.

Driven through ``DjangoAGUIView`` with ``DefaultStepStore`` and a conversation
store that persists, which is the only path a resume takes in a deployment: the
first run records a snapshot, ``resume/`` or ``fork/`` seeds the next run from
it, and the save replaces the whole row. Two things are held here.

The model sees the stored history once, however much of its transcript the
client replays, and a resubmitted or edited user message replaces the turn it
names rather than adding a copy; the row reads the way the run did.

The ids a client keyed earlier messages by survive the save: the earlier
answer keeps the id it streamed under, after one resume and after two, and so
does a tool result folded into a request ahead of another.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.types.conversation import Conversation
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.agent.test_agui_view import _drain, _post, _registry, _ThreadStore


def _body(
    messages: list[dict[str, Any]], *, run_id: str, thread_id: str = "t1", **extra: Any
) -> bytes:
    return json.dumps(
        {
            "threadId": thread_id,
            "runId": run_id,
            "state": {},
            "messages": messages,
            "tools": extra.get("tools", []),
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


def _user(message_id: str, content: str) -> dict[str, Any]:
    return {"id": message_id, "role": "user", "content": content}


def _frames(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


def _streamed_answer_ids(body: str) -> list[str]:
    """The ids of the text messages a run announced that carried text."""
    frames = _frames(body)
    texted = {frame["messageId"] for frame in frames if frame["type"] == "TEXT_MESSAGE_CONTENT"}
    return [
        frame["messageId"]
        for frame in frames
        if frame["type"] == "TEXT_MESSAGE_START" and frame["messageId"] in texted
    ]


def _prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]


def _recording_model(seen: list[list[ModelMessage]], answer: str = "done") -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: Any) -> Any:
        seen.append(list(messages))
        yield answer

    return FunctionModel(stream_function=stream)


def _row(store: _ThreadStore, thread_id: str = "t1") -> list[dict[str, Any]]:
    return store.rows[thread_id].messages


def _contents(row: list[dict[str, Any]], role: str) -> list[Any]:
    return [message.get("content") for message in row if message["role"] == role]


def _assert_no_id_twice(row: list[dict[str, Any]]) -> None:
    counts = Counter(message["id"] for message in row)
    assert [message_id for message_id, n in counts.items() if n > 1] == []


async def _user_named(username: str) -> Any:
    return await sync_to_async(get_user_model().objects.create)(username=username)


def _view(user: Any, store: _ThreadStore, model: Any) -> DjangoAGUIView:
    return DjangoAGUIView(
        _registry(),
        model=model,
        step_store=DefaultStepStore,
        conversation_store=store,
        get_user=lambda _request: user,
    )


# --- What the model sees, and what the row keeps --------------------------------


@pytest.mark.django_db(transaction=True)
async def test_a_replayed_transcript_reaches_the_model_once() -> None:
    # A third-party client that replays its whole transcript on a resume. The
    # snapshot already holds the first two messages, so before the server held
    # the history the model saw them twice and the row stored them twice.
    seen: list[list[ModelMessage]] = []
    store = _ThreadStore()
    view = _view(await _user_named("replayer"), store, _recording_model(seen))
    first = await _drain(await view(_post(_body([_user("u1", "warm up")], run_id="r1"))))
    (answer_id,) = _streamed_answer_ids(first)

    replayed = [
        _user("u1", "warm up"),
        {"id": answer_id, "role": "assistant", "content": "done"},
        _user("u2", "next"),
    ]
    await _drain(await view(_post(_body(replayed, run_id="r2")), resume_from="r1"))

    assert _prompts(seen[-1]) == ["warm up", "next"]
    row = _row(store)
    assert _contents(row, "user") == ["warm up", "next"]
    assert _contents(row, "assistant") == ["done", "done"]
    _assert_no_id_twice(row)


@pytest.mark.django_db(transaction=True)
async def test_an_edited_message_replaces_the_turn_it_names() -> None:
    # The client edits its first message: same id, new text. The run replaces
    # that turn, so neither the model nor the row holds the old text.
    seen: list[list[ModelMessage]] = []
    store = _ThreadStore()
    view = _view(await _user_named("editor"), store, _recording_model(seen))
    await _drain(await view(_post(_body([_user("u1", "double 5")], run_id="r1"))))

    await _drain(await view(_post(_body([_user("u1", "double 6")], run_id="r2")), resume_from="r1"))

    assert _prompts(seen[-1]) == ["double 6"]
    row = _row(store)
    assert _contents(row, "user") == ["double 6"]
    assert [message["id"] for message in row if message["role"] == "user"] == ["u1"]
    _assert_no_id_twice(row)


@pytest.mark.django_db(transaction=True)
async def test_a_resubmitted_message_reaches_the_model_once() -> None:
    # A retry: the same message, under the same id. The run replaces the turn
    # it names rather than following it with a copy.
    seen: list[list[ModelMessage]] = []
    store = _ThreadStore()
    view = _view(await _user_named("resubmitter"), store, _recording_model(seen))
    await _drain(await view(_post(_body([_user("u1", "double 5")], run_id="r1"))))

    await _drain(await view(_post(_body([_user("u1", "double 5")], run_id="r2")), resume_from="r1"))

    assert _prompts(seen[-1]) == ["double 5"]
    row = _row(store)
    assert _contents(row, "user") == ["double 5"]
    assert _contents(row, "assistant") == ["done"]
    _assert_no_id_twice(row)


@pytest.mark.django_db(transaction=True)
async def test_an_edited_later_message_cuts_the_history_there() -> None:
    # An edit of the second message, posted with the transcript before it: the
    # history is cut just before the turn it names and the earlier exchange is
    # kept, once.
    seen: list[list[ModelMessage]] = []
    store = _ThreadStore()
    view = _view(await _user_named("retrier"), store, _recording_model(seen))
    first = await _drain(await view(_post(_body([_user("u1", "first")], run_id="r1"))))
    (answer_id,) = _streamed_answer_ids(first)
    await _drain(await view(_post(_body([_user("u2", "second")], run_id="r2")), resume_from="r1"))

    retried = [
        _user("u1", "first"),
        {"id": answer_id, "role": "assistant", "content": "done"},
        _user("u2", "second, again"),
    ]
    await _drain(await view(_post(_body(retried, run_id="r3")), resume_from="r2"))

    assert _prompts(seen[-1]) == ["first", "second, again"]
    row = _row(store)
    assert _contents(row, "user") == ["first", "second, again"]
    assert _contents(row, "assistant") == ["done", "done"]
    _assert_no_id_twice(row)


# --- The ids a client keyed earlier messages by ---------------------------------


@pytest.mark.django_db(transaction=True)
async def test_the_earlier_answer_keeps_its_streamed_id_after_one_resume_and_after_two() -> None:
    store = _ThreadStore()
    view = _view(await _user_named("rater"), store, TestModel())
    first = await _drain(await view(_post(_body([_user("u1", "double 5")], run_id="r1"))))
    (answer_id,) = _streamed_answer_ids(first)
    stored_first = list(_row(store))

    await _drain(await view(_post(_body([_user("u2", "double 6")], run_id="r2")), resume_from="r1"))
    after_one = _row(store)
    await _drain(await view(_post(_body([_user("u3", "double 7")], run_id="r3")), resume_from="r2"))
    after_two = _row(store)

    # Every message the first run stored is stored under the same id after
    # each resume -- the answer, the tool call that preceded it and its result.
    assert [m["id"] for m in after_one[: len(stored_first)]] == [m["id"] for m in stored_first]
    assert [m["id"] for m in after_two[: len(stored_first)]] == [m["id"] for m in stored_first]
    assert answer_id in [m["id"] for m in after_two]
    _assert_no_id_twice(after_one)
    _assert_no_id_twice(after_two)


@pytest.mark.django_db(transaction=True)
async def test_a_fork_into_a_new_thread_keeps_the_sources_ids() -> None:
    store = _ThreadStore()
    view = _view(await _user_named("forker"), store, TestModel())
    await _drain(await view(_post(_body([_user("u1", "double 5")], run_id="r1", thread_id="tA"))))
    source = list(_row(store, "tA"))

    await _drain(
        await view(
            _post(_body([_user("u2", "double 6")], run_id="r2", thread_id="tB")), resume_from="r1"
        )
    )

    forked = _row(store, "tB")
    assert [m["id"] for m in forked[: len(source)]] == [m["id"] for m in source]
    # The source thread is not written by a fork.
    assert _row(store, "tA") == source
    _assert_no_id_twice(forked)


def _frontend_tool_model() -> FunctionModel:
    """Two client-side tool calls in one response, then an answer once both return."""

    async def stream(messages: list[ModelMessage], info: Any) -> Any:
        last = messages[-1]
        if isinstance(last, ModelRequest) and any(
            isinstance(part, ToolReturnPart) for part in last.parts
        ):
            yield "both picked"
            return
        if isinstance(last, ModelRequest) and any(
            isinstance(part, UserPromptPart) and part.content == "next" for part in last.parts
        ):
            yield "next done"
            return
        yield {0: DeltaToolCall(name="pick", json_args='{"n": 1}', tool_call_id="call-1")}
        yield {1: DeltaToolCall(name="pick", json_args='{"n": 2}', tool_call_id="call-2")}

    return FunctionModel(stream_function=stream)


_PICK = {
    "name": "pick",
    "description": "Pick a number in the browser.",
    "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}},
}


@pytest.mark.django_db(transaction=True)
async def test_two_posted_tool_results_for_one_response_keep_their_ids() -> None:
    # The client answers a response's two calls in one post. They load into one
    # request, which keeps only the last id, so the first result came back from
    # every later save under a new one. The calls end their run without a
    # continuable snapshot, so the client answers them on the plain endpoint,
    # with the thread as the server stored it, and resumes from that run.
    store = _ThreadStore()
    view = _view(await _user_named("picker"), store, _frontend_tool_model())
    await _drain(await view(_post(_body([_user("u1", "pick two")], run_id="r1", tools=[_PICK]))))
    results = [
        {"id": "result-1", "role": "tool", "toolCallId": "call-1", "content": "1"},
        {"id": "result-2", "role": "tool", "toolCallId": "call-2", "content": "2"},
    ]
    await _drain(await view(_post(_body([*_row(store), *results], run_id="r2", tools=[_PICK]))))
    await _drain(
        await view(
            _post(_body([_user("u2", "next")], run_id="r3", tools=[_PICK])), resume_from="r2"
        )
    )

    row = _row(store)
    assert [m["id"] for m in row if m["role"] == "tool"] == ["result-1", "result-2"]
    assert _contents(row, "assistant")[-1] == "next done"
    _assert_no_id_twice(row)


@pytest.mark.django_db(transaction=True)
async def test_a_row_that_no_longer_matches_lends_no_id() -> None:
    # The row was overwritten after the snapshot was taken, so it describes a
    # different conversation: nothing in it names the snapshot's messages, and
    # none of its ids is lent to them.
    seen: list[list[ModelMessage]] = []
    store = _ThreadStore()
    view = _view(await _user_named("diverger"), store, _recording_model(seen))
    first = await _drain(await view(_post(_body([_user("u1", "warm up")], run_id="r1"))))
    (answer_id,) = _streamed_answer_ids(first)
    store.rows["t1"] = Conversation(
        thread_id="t1",
        messages=[
            {"id": "other-user", "role": "user", "content": "something else"},
            {"id": answer_id, "role": "assistant", "content": "a different answer"},
        ],
        owner_id=store.rows["t1"].owner_id,
    )

    await _drain(await view(_post(_body([_user("u2", "next")], run_id="r2")), resume_from="r1"))

    row = _row(store)
    assert _contents(row, "assistant")[0] == "done"
    assert answer_id not in [m["id"] for m in row]
    _assert_no_id_twice(row)


class _RefusingStore(_ThreadStore):
    """Saves, but refuses to read a row back, as a store refusing an anonymous caller does."""

    async def load(self, thread_id: str, *, request: Any) -> Any:
        raise AnonymousOperationError("anonymous")


@pytest.mark.parametrize(
    "lose_the_row",
    [
        pytest.param(lambda store: store.rows.pop("t1"), id="deleted"),
        pytest.param(
            lambda store: store.rows.update(
                t1=Conversation(thread_id="t1", messages=[{"id": "x", "role": "nobody"}])
            ),
            id="unreadable",
        ),
        pytest.param(None, id="refused"),
    ],
)
@pytest.mark.django_db(transaction=True)
async def test_a_row_that_cannot_be_read_costs_the_ids_not_the_run(lose_the_row: Any) -> None:
    store = _RefusingStore() if lose_the_row is None else _ThreadStore()
    view = _view(await _user_named("loser"), store, _recording_model([]))
    first = await _drain(await view(_post(_body([_user("u1", "warm up")], run_id="r1"))))
    (answer_id,) = _streamed_answer_ids(first)
    if lose_the_row is not None:
        lose_the_row(store)

    resumed = await _drain(
        await view(_post(_body([_user("u2", "next")], run_id="r2")), resume_from="r1")
    )

    assert "RUN_FINISHED" in resumed
    row = _row(store)
    assert _contents(row, "user") == ["warm up", "next"]
    assert answer_id not in [m["id"] for m in row]
