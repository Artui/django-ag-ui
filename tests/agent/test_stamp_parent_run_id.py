from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from ag_ui.core import BaseEvent, RunFinishedEvent, RunStartedEvent

from django_ag_ui.agent.stamp_parent_run_id import stamp_parent_run_id
from tests.agent.test_agui_view import _drain, _post, _ThreadStore
from tests.agent.test_resumed_history_through_the_view import (
    _body,
    _frames,
    _recording_model,
    _user,
    _user_named,
    _view,
)


async def _events(*events: BaseEvent) -> AsyncIterator[BaseEvent]:
    for event in events:
        yield event


async def _stamped(parent_run_id: str | None, *events: BaseEvent) -> list[BaseEvent]:
    return [event async for event in stamp_parent_run_id(_events(*events), parent_run_id)]


async def test_the_run_started_event_names_the_parent_run() -> None:
    finished = RunFinishedEvent(thread_id="t", run_id="r2")

    events = await _stamped("r1", RunStartedEvent(thread_id="t", run_id="r2"), finished)

    assert isinstance(events[0], RunStartedEvent)
    assert events[0].parent_run_id == "r1"
    assert events[1] is finished


async def test_a_parent_the_adapter_already_set_is_kept() -> None:
    started = RunStartedEvent(thread_id="t", run_id="r2", parent_run_id="upstream")

    (event,) = await _stamped("r1", started)

    assert event is started


async def test_a_run_with_no_parent_passes_through() -> None:
    started = RunStartedEvent(thread_id="t", run_id="r1")

    (event,) = await _stamped(None, started)

    assert event is started


def _started(body: str) -> dict[str, Any]:
    return next(frame for frame in _frames(body) if frame["type"] == "RUN_STARTED")


@pytest.mark.django_db(transaction=True)
async def test_resume_and_fork_name_their_source_run_on_the_wire() -> None:
    store = _ThreadStore()
    view = _view(await _user_named("lineage"), store, _recording_model([]))
    first = await _drain(await view(_post(_body([_user("u1", "hi")], run_id="r1"))))
    resumed = await _drain(
        await view(_post(_body([_user("u2", "more")], run_id="r2")), resume_from="r1")
    )
    forked = await _drain(
        await view(
            _post(_body([_user("u3", "fork")], run_id="r3", thread_id="t2")), resume_from="r1"
        )
    )

    assert "parentRunId" not in _started(first)
    assert _started(resumed)["parentRunId"] == "r1"
    assert _started(forked)["parentRunId"] == "r1"
