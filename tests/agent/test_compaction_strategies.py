from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import (
    CompactOnRequest,
    RelevanceCompaction,
    SlidingWindowCompaction,
    SummarizingCompaction,
)

from django_ag_ui import COMPACTION_ACTIVITY_TYPE, CompactionObserver
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.constants import DETACH_PROP
from tests.agent.test_agui_view import _drain, _post, _registry, _ThreadStore
from tests.agent.test_resumed_history_through_the_view import (
    _activity_snapshots,
    _assert_no_id_twice,
    _body,
    _frames,
    _row,
    _user,
    _user_named,
)

_SUMMARY_MARKER = "pydantic-ai-harness.compaction.summary.v1"


def _long_thread(turns: int = 12) -> list[dict[str, Any]]:
    thread: list[dict[str, Any]] = []
    for turn in range(turns):
        thread.append(_user(f"u{turn}", f"question {turn} " + "x" * 400))
        thread.append({"id": f"a{turn}", "role": "assistant", "content": f"answer {turn}"})
    return [*thread, _user("last", "and now?")]


def _summarizer() -> FunctionModel:
    def run(messages: list[ModelMessage], info: Any) -> ModelResponse:
        return ModelResponse(parts=[TextPart("SUMMARY")])

    return FunctionModel(run)


def _answers(messages: list[ModelMessage], info: Any) -> Any:
    return _stream("done")


def _asks_once(messages: list[ModelMessage], info: Any) -> Any:
    asked = any(
        isinstance(part, ToolReturnPart) and part.tool_name == "compact_context"
        for message in messages
        for part in message.parts
    )
    if asked:
        return _stream("done")
    call_id = f"c{len(messages)}"
    return _stream({0: DeltaToolCall(name="compact_context", json_args="{}", tool_call_id=call_id)})


async def _stream(chunk: Any) -> Any:
    yield chunk


def _sliding() -> AbstractCapability[Any]:
    return SlidingWindowCompaction(max_messages=10, keep_messages=4)


def _relevance() -> AbstractCapability[Any]:
    return RelevanceCompaction(
        TestModel(),
        summarizer=SummarizingCompaction(model=_summarizer(), max_messages=1),
        max_tokens=500,
        target_tokens=200,
        keep_tokens=50,
    )


def _on_request() -> AbstractCapability[Any]:
    return CompactOnRequest(
        SummarizingCompaction(model=_summarizer(), max_messages=1, keep_messages=3)
    )


_STRATEGIES = pytest.mark.parametrize(
    ("strategy", "behaviour"),
    [(_sliding, _answers), (_relevance, _answers), (_on_request, _asks_once)],
    ids=["sliding", "relevance", "on_request"],
)


def _recorded(behaviour: Any, seen: list[list[ModelMessage]]) -> Any:
    def stream(messages: list[ModelMessage], info: Any) -> Any:
        seen.append(list(messages))
        return behaviour(messages, info)

    return stream


async def _view(
    name: str,
    strategy: Callable[[], AbstractCapability[Any]],
    behaviour: Any,
    seen: list[list[ModelMessage]] | None = None,
) -> tuple[DjangoAGUIView, _ThreadStore]:
    store = _ThreadStore()
    user = await _user_named(name)
    view = DjangoAGUIView(
        _registry(),
        model=FunctionModel(stream_function=_recorded(behaviour, [] if seen is None else seen)),
        step_store=DefaultStepStore,
        conversation_store=store,
        get_user=lambda _request: user,
        capabilities=[CompactionObserver(strategy())],
    )
    return view, store


@_STRATEGIES
@pytest.mark.django_db(transaction=True)
async def test_each_strategy_compacts_a_long_thread_on_the_wire(
    strategy: Callable[[], AbstractCapability[Any]], behaviour: Any
) -> None:
    view, _ = await _view("compactor", strategy, behaviour)
    body = await _drain(await view(_post(_body(_long_thread(), run_id="r1"))))

    (snapshot,) = _activity_snapshots(body)
    assert snapshot["activityType"] == COMPACTION_ACTIVITY_TYPE
    assert snapshot["content"]["before"] > snapshot["content"]["after"]


def _ids(row: list[dict[str, Any]]) -> list[str]:
    return [message["id"] for message in row]


def _prompts(messages: list[ModelMessage]) -> list[Any]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


@_STRATEGIES
@pytest.mark.django_db(transaction=True)
async def test_each_strategy_reloads_every_earlier_turn_under_stable_ids(
    strategy: Callable[[], AbstractCapability[Any]], behaviour: Any
) -> None:
    view, store = await _view("reloader", strategy, behaviour)
    await _drain(await view(_post(_body(_long_thread(), run_id="r1"))))
    first = _row(store)
    await _drain(await view(_post(_body([_user("u-2", "more")], run_id="r2")), resume_from="r1"))
    second = _row(store)
    await _drain(await view(_post(_body([_user("u-3", "again")], run_id="r3")), resume_from="r2"))
    third = _row(store)

    assert _ids(first)[:25] == [m["id"] for m in _long_thread()]
    assert _ids(second)[: len(first)] == _ids(first)
    assert second[len(first)]["id"] == "u-2"
    assert _ids(third)[: len(second)] == _ids(second)
    assert third[len(second)]["id"] == "u-3"
    assert third[-1] == {**third[-1], "role": "assistant", "content": "done"}
    _assert_no_id_twice(third)
    assert not [m for m in third if m["role"] == "activity" and _SUMMARY_MARKER in str(m)]


_SUMMARIZES = pytest.mark.parametrize(
    ("strategy", "behaviour", "summarizes"),
    [(_sliding, _answers, False), (_relevance, _answers, True), (_on_request, _asks_once, True)],
    ids=["sliding", "relevance", "on_request"],
)


@_SUMMARIZES
@pytest.mark.django_db(transaction=True)
async def test_each_strategy_sends_the_model_only_the_compacted_history_on_a_resume(
    strategy: Callable[[], AbstractCapability[Any]], behaviour: Any, summarizes: bool
) -> None:
    seen: list[list[ModelMessage]] = []
    view, store = await _view("viewer", strategy, behaviour, seen)
    await _drain(await view(_post(_body(_long_thread(), run_id="r1"))))
    seen.clear()
    await _drain(await view(_post(_body([_user("u-2", "more")], run_id="r2")), resume_from="r1"))

    request = seen[0]
    assert _prompts(request)[-1] == "more"
    assert not any(str(prompt).startswith("question 5 ") for prompt in _prompts(request))
    assert len(request) < len(_row(store))
    compacted = any(
        isinstance(part, CompactionPart) for message in request for part in message.parts
    )
    assert compacted is summarizes


def _loops_six_times() -> Any:
    calls = [0]

    def behaviour(messages: list[ModelMessage], info: Any) -> Any:
        calls[0] += 1
        if calls[0] > 6:
            return _stream("done")
        call = DeltaToolCall(name="double", json_args='{"n": 2}', tool_call_id=f"d{calls[0]}")
        return _stream({0: call})

    return behaviour


@pytest.mark.django_db(transaction=True)
async def test_a_run_that_compacts_its_own_steps_stores_every_step_it_streamed() -> None:
    compacting = lambda: SlidingWindowCompaction(max_messages=6, keep_messages=2)  # noqa: E731
    view, store = await _view("looper", compacting, _loops_six_times())
    body = await _drain(await view(_post(_body([_user("u1", "loop")], run_id="r1"))))

    streamed = [f["messageId"] for f in _frames(body) if f["type"] == "TOOL_CALL_RESULT"]
    row = _row(store)
    called = [call["id"] for m in row for call in m.get("toolCalls") or []]
    assert called == [f"d{n}" for n in range(1, 7)]
    assert [m["id"] for m in row if m["role"] == "tool"] == streamed
    assert row[-1]["content"] == "done"
    _assert_no_id_twice(row)


def _thinks_six_times() -> Any:
    calls = [0]

    def behaviour(messages: list[ModelMessage], info: Any) -> Any:
        calls[0] += 1
        if calls[0] > 6:
            return _stream("done")
        call = DeltaToolCall(name="double", json_args='{"n": 2}', tool_call_id=f"d{calls[0]}")
        return _stream({0: DeltaThinkingPart(content=f"thought {calls[0]}"), 1: call})

    return behaviour


@pytest.mark.django_db(transaction=True)
async def test_a_run_that_compacts_its_own_steps_keeps_its_reasoning_in_streamed_order() -> None:
    compacting = lambda: SlidingWindowCompaction(max_messages=6, keep_messages=2)  # noqa: E731
    view, store = await _view("thinker", compacting, _thinks_six_times())
    body = await _drain(await view(_post(_body([_user("u1", "loop")], run_id="r1"))))

    frames = _frames(body)
    thoughts = [f["messageId"] for f in frames if f["type"] == "REASONING_START"]
    results = [f["messageId"] for f in frames if f["type"] == "TOOL_CALL_RESULT"]
    run = _row(store)[1:-1]
    assert [m["role"] for m in run] == ["reasoning", "assistant", "tool"] * 6
    assert [(m["id"], m["content"]) for m in run if m["role"] == "reasoning"] == [
        (thoughts[n], f"thought {n + 1}") for n in range(6)
    ]
    assert [call["id"] for m in run for call in m.get("toolCalls") or []] == [
        f"d{n}" for n in range(1, 7)
    ]
    assert [m["id"] for m in run if m["role"] == "tool"] == results


@_STRATEGIES
@pytest.mark.django_db(transaction=True)
async def test_an_edit_of_a_compacted_away_turn_is_refused_before_the_run(
    strategy: Callable[[], AbstractCapability[Any]], behaviour: Any
) -> None:
    seen: list[list[ModelMessage]] = []
    view, store = await _view("editor", strategy, behaviour, seen)
    await _drain(await view(_post(_body(_long_thread(), run_id="r1"))))
    before = _row(store)
    seen.clear()
    edited = [_user("u5", "question 5, edited")]
    forked = _post(_body(edited, run_id="r2", thread_id="t2"))
    body = await _drain(await view(forked, resume_from="r1"))

    (error,) = [frame for frame in _frames(body) if frame["type"] == "RUN_ERROR"]
    assert "compacted" in error["message"]
    assert seen == []
    assert _row(store) == before
    assert "t2" not in store.rows


@_STRATEGIES
@pytest.mark.django_db(transaction=True)
async def test_an_edit_of_a_turn_the_compacted_history_keeps_runs(
    strategy: Callable[[], AbstractCapability[Any]], behaviour: Any
) -> None:
    view, store = await _view("keeper", strategy, behaviour)
    await _drain(await view(_post(_body(_long_thread(), run_id="r1"))))
    edited = [_user("last", "and now, edited?")]
    forked = _post(_body(edited, run_id="r2", thread_id="t2"))
    body = await _drain(await view(forked, resume_from="r1"))

    assert [frame for frame in _frames(body) if frame["type"] == "RUN_ERROR"] == []
    row = _row(store, "t2")
    earlier = [m["id"] for m in _long_thread()][:-1]
    assert _ids(row)[: len(earlier)] == earlier
    assert row[len(earlier)] == {**row[len(earlier)], "id": "last", "content": "and now, edited?"}
    assert row[-1]["content"] == "done"
    _assert_no_id_twice(row)


class _Queue:
    """A queued-start seam that records what it was asked to queue."""

    def __init__(self) -> None:
        self.queued: list[str] = []

    async def enqueue(self, request: Any, thread_id: str, run_id: str, stored: Any) -> None:
        self.queued.append(run_id)


class _Runs(DefaultRunStore):
    """Records the run ids it is asked to finish."""

    def __init__(self) -> None:
        super().__init__()
        self.finished: list[str] = []

    async def finish(self, thread_id: str, run_id: str, *args: Any, **kwargs: Any) -> Any:
        self.finished.append(run_id)
        return await super().finish(thread_id, run_id, *args, **kwargs)


@pytest.mark.parametrize(("detach", "queued"), [(False, False), (True, False), (True, True)])
@pytest.mark.django_db(transaction=True)
async def test_a_refused_edit_leaves_the_threads_status_and_the_next_turn_runs(
    detach: bool, queued: bool
) -> None:
    queue = _Queue()
    store, runs, user = _ThreadStore(), _Runs(), await _user_named("refused")
    view = DjangoAGUIView(
        _registry(),
        model=FunctionModel(stream_function=_recorded(_answers, [])),
        step_store=DefaultStepStore,
        conversation_store=store,
        run_store=runs,
        get_user=lambda _request: user,
        capabilities=[CompactionObserver(_sliding())],
        config=build_ag_ui_config(run_timeout_seconds=30),
        queued=cast("Any", queue) if queued else None,
    )
    await _drain(await view(_post(_body(_long_thread(), run_id="r1"))))
    request = AsyncRequestFactory().get("/")
    request.user = user
    before = (await runs.get_many(["t1"], request=request))["t1"], _row(store)
    edited = json.loads(_body([_user("u5", "edited")], run_id="r2"))
    edited["forwardedProps"] = {DETACH_PROP: detach}
    refused = await _drain(await view(_post(json.dumps(edited).encode()), resume_from="r1"))
    assert [frame["type"] for frame in _frames(refused)][-1] == "RUN_ERROR"
    assert ((await runs.get_many(["t1"], request=request))["t1"], _row(store)) == before
    assert (queue.queued, runs.finished) == ([], ["r1"])

    body = await _drain(
        await view(_post(_body([_user("u-3", "next")], run_id="r3")), resume_from="r1")
    )
    assert [frame for frame in _frames(body) if frame["type"] == "RUN_ERROR"] == []
    assert (await runs.get_many(["t1"], request=request))["t1"].last_run_id == "r3"
