from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.compaction import SlidingWindowCompaction

from django_ag_ui.agent.compaction_observer import (
    COMPACTION_SINK,
    Compaction,
    CompactionObserver,
)

# One model, shared by the run context and every request context below. A
# strategy asks whether the request's model is the run's model to find out
# whether some other capability swapped it mid-run; sharing an instance is what
# makes these tests the ordinary "nobody swapped it" case.
_MODEL = TestModel()


def _run_context(messages: list[Any]) -> RunContext[None]:
    """The genuine upstream run context, for the reason the request context is."""
    return RunContext(deps=None, model=_MODEL, usage=RunUsage(), messages=messages)


def _request_context(messages: list[Any]) -> ModelRequestContext:
    """The genuine upstream context rather than a stand-in."""
    return ModelRequestContext(
        model=_MODEL,
        messages=messages,
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )


def _messages(count: int) -> list[Any]:
    """A history of genuine messages, alternating user turn and model reply."""
    return [
        ModelRequest(parts=[UserPromptPart(content=f"user {index}")])
        if index % 2 == 0
        else ModelResponse(parts=[TextPart(content=f"model {index}")])
        for index in range(count)
    ]


def _contexts(count: int) -> tuple[RunContext[None], ModelRequestContext]:
    """A run context and a request context over one history of ``count`` messages."""
    messages = _messages(count)
    return _run_context(messages), _request_context(messages)


class _Stub:
    """The attributes ``WrapperCapability`` reads off whatever it wraps."""

    id: str | None = None
    defer_loading: bool = False


class _Passthrough(_Stub):
    """A capability that leaves the history alone."""

    async def before_model_request(self, ctx: Any, request_context: Any) -> Any:
        return request_context


class _Rewriter(_Stub):
    """A capability that rewrites history without shortening it."""

    async def before_model_request(self, ctx: Any, request_context: Any) -> Any:
        request_context.messages = _messages(len(request_context.messages))
        return request_context


@pytest.fixture
def sink() -> Any:
    recorded: list[Compaction] = []
    token = COMPACTION_SINK.set(recorded)
    yield recorded
    COMPACTION_SINK.reset(token)


async def test_records_a_real_compaction(sink: list[Compaction]) -> None:
    observer = CompactionObserver(SlidingWindowCompaction(max_messages=4, keep_messages=2))
    result = await observer.before_model_request(*_contexts(10))
    after = len(result.messages)
    assert len(sink) == 1
    # How long a tail is kept is upstream's policy, and it has moved once already.
    assert after < 10
    assert sink[0].before == 10
    assert sink[0].after == after
    assert sink[0].removed == 10 - after


async def test_below_the_threshold_records_nothing(sink: list[Compaction]) -> None:
    observer = CompactionObserver(SlidingWindowCompaction(max_messages=100))
    await observer.before_model_request(*_contexts(3))
    assert sink == []


async def test_a_capability_that_does_not_shorten_records_nothing(sink: list[Compaction]) -> None:
    # Only message counts are exposed, so an in-place rewrite is invisible, which is honest here.
    observer = CompactionObserver(_Rewriter())
    await observer.before_model_request(*_contexts(5))
    assert sink == []


async def test_passthrough_capability_records_nothing(sink: list[Compaction]) -> None:
    observer = CompactionObserver(_Passthrough())
    await observer.before_model_request(*_contexts(5))
    assert sink == []


async def test_without_a_sink_the_observer_is_inert() -> None:
    # Outside this transport there is no stream; recording must not blow up or leak.
    assert COMPACTION_SINK.get() is None
    observer = CompactionObserver(SlidingWindowCompaction(max_messages=4, keep_messages=2))
    result = await observer.before_model_request(*_contexts(10))
    assert len(result.messages) < 10


async def test_wrapping_does_not_change_the_compaction_itself(sink: list[Compaction]) -> None:
    bare = SlidingWindowCompaction(max_messages=4, keep_messages=2)
    wrapped = CompactionObserver(SlidingWindowCompaction(max_messages=4, keep_messages=2))
    bare_result = await bare.before_model_request(*_contexts(10))
    wrapped_result = await wrapped.before_model_request(*_contexts(10))
    assert len(wrapped_result.messages) == len(bare_result.messages)


def test_the_wrapped_capability_stays_reachable() -> None:
    # WrapperCapability delegates the rest of the protocol; losing that drops ordering silently.
    inner = SlidingWindowCompaction(max_messages=4)
    assert CompactionObserver(inner).wrapped is inner
