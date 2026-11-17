from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import (
    CompactOnRequest,
    RelevanceCompaction,
    SummarizingCompaction,
)

from django_ag_ui import COMPACTION_ACTIVITY_TYPE, CompactionObserver
from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.agent.test_agui_view import _drain, _post, _registry, _ThreadStore
from tests.agent.test_resumed_history_through_the_view import (
    _activity_snapshots,
    _assert_no_id_twice,
    _body,
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
    return _stream({0: DeltaToolCall(name="compact_context", json_args="{}", tool_call_id="c1")})


async def _stream(chunk: Any) -> Any:
    yield chunk


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
    [(_relevance, _answers), (_on_request, _asks_once)],
    ids=["relevance", "on_request"],
)


async def _view(
    name: str, strategy: Callable[[], AbstractCapability[Any]], behaviour: Any
) -> tuple[DjangoAGUIView, _ThreadStore]:
    store = _ThreadStore()
    user = await _user_named(name)
    view = DjangoAGUIView(
        _registry(),
        model=FunctionModel(stream_function=behaviour),
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


@_STRATEGIES
@pytest.mark.django_db(transaction=True)
async def test_each_strategy_reloads_its_stored_transcript_under_stable_ids(
    strategy: Callable[[], AbstractCapability[Any]], behaviour: Any
) -> None:
    view, store = await _view("reloader", strategy, behaviour)
    await _drain(await view(_post(_body(_long_thread(), run_id="r1"))))
    await _drain(await view(_post(_body([_user("u-2", "more")], run_id="r2")), resume_from="r1"))
    compacted = _row(store)
    await _drain(await view(_post(_body([_user("u-3", "again")], run_id="r3")), resume_from="r2"))
    reloaded = _row(store)

    assert [m["id"] for m in reloaded][: len(compacted)] == [m["id"] for m in compacted]
    assert compacted[0]["id"] == "u0"
    _assert_no_id_twice(reloaded)
    (summary,) = [m for m in reloaded if m["role"] == "activity"]
    assert summary["content"]["metadata"][_SUMMARY_MARKER] is True
