"""``CompactionObserver`` — recording a wrapped capability's trims.

The observer's whole job is to notice something upstream deliberately does not
announce, so these pin the noticing: what counts as a compaction, what does not,
and that wrapping leaves the wrapped capability's behaviour alone.
"""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.trace import NoOpTracer
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import SlidingWindow

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


class _Ctx:
    """The slice of ``RunContext`` a compaction strategy actually touches."""

    tracer = NoOpTracer()
    model = _MODEL


def _request_context(messages: list[Any]) -> ModelRequestContext:
    """The genuine upstream context rather than a stand-in.

    A hand-rolled double carrying only ``messages`` used to stand in here, and it
    broke the moment a strategy started reading ``model`` off the context — a
    failure about our double, not about the observer. The real dataclass costs
    four keywords to build and cannot drift from the contract it models.
    """
    return ModelRequestContext(
        model=_MODEL,
        messages=messages,
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )


class _Stub:
    """The attributes ``WrapperCapability`` reads off whatever it wraps.

    It adopts the wrapped capability's ``id`` and ``defer_loading`` so a wrapper
    can sit over a *deferred* capability without losing its deferral or its place
    in the load catalog — so a stub has to carry both.
    """

    id: str | None = None
    defer_loading: bool = False


class _Passthrough(_Stub):
    """A capability that leaves the history alone."""

    async def before_model_request(self, ctx: Any, request_context: Any) -> Any:
        return request_context


class _Rewriter(_Stub):
    """A capability that rewrites history without shortening it."""

    async def before_model_request(self, ctx: Any, request_context: Any) -> Any:
        request_context.messages = ["rewritten"] * len(request_context.messages)
        return request_context


@pytest.fixture
def sink() -> Any:
    recorded: list[Compaction] = []
    token = COMPACTION_SINK.set(recorded)
    yield recorded
    COMPACTION_SINK.reset(token)


async def test_records_a_real_compaction(sink: list[Compaction]) -> None:
    observer = CompactionObserver(SlidingWindow(max_messages=4, keep_messages=2))
    await observer.before_model_request(_Ctx(), _request_context(["m"] * 10))
    assert len(sink) == 1
    assert sink[0].before == 10
    assert sink[0].after == 2
    assert sink[0].removed == 8


async def test_below_the_threshold_records_nothing(sink: list[Compaction]) -> None:
    observer = CompactionObserver(SlidingWindow(max_messages=100))
    await observer.before_model_request(_Ctx(), _request_context(["m"] * 3))
    assert sink == []


async def test_a_capability_that_does_not_shorten_records_nothing(sink: list[Compaction]) -> None:
    # The seam only exposes message counts, so an in-place rewrite is invisible —
    # which is honest: the indicator claims turns were dropped, and none were.
    observer = CompactionObserver(_Rewriter())
    await observer.before_model_request(_Ctx(), _request_context(["m"] * 5))
    assert sink == []


async def test_passthrough_capability_records_nothing(sink: list[Compaction]) -> None:
    observer = CompactionObserver(_Passthrough())
    await observer.before_model_request(_Ctx(), _request_context(["m"] * 5))
    assert sink == []


async def test_without_a_sink_the_observer_is_inert() -> None:
    # A capability used outside this transport (a management command, a test)
    # has no stream to report to; recording must not blow up or leak.
    assert COMPACTION_SINK.get() is None
    observer = CompactionObserver(SlidingWindow(max_messages=4, keep_messages=2))
    request_context = _request_context(["m"] * 10)
    result = await observer.before_model_request(_Ctx(), request_context)
    assert len(result.messages) == 2


async def test_wrapping_does_not_change_the_compaction_itself(sink: list[Compaction]) -> None:
    bare = SlidingWindow(max_messages=4, keep_messages=2)
    wrapped = CompactionObserver(SlidingWindow(max_messages=4, keep_messages=2))
    bare_result = await bare.before_model_request(_Ctx(), _request_context(["m"] * 10))
    wrapped_result = await wrapped.before_model_request(_Ctx(), _request_context(["m"] * 10))
    assert len(wrapped_result.messages) == len(bare_result.messages)


def test_the_wrapped_capability_stays_reachable() -> None:
    # ``WrapperCapability`` delegates the rest of the protocol; losing that would
    # silently drop ordering and hook-introspection.
    inner = SlidingWindow(max_messages=4)
    assert CompactionObserver(inner).wrapped is inner
