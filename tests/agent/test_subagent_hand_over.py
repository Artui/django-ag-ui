from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from ag_ui.core import BaseEvent, RunStartedEvent
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.sub_agents import SubAgent, SubAgents, TaskControl

from django_ag_ui import SubAgentObserver
from django_ag_ui.agent.inject_subagent_events import inject_subagent_events
from django_ag_ui.agent.live_tasks import RUN_TASKS
from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK, SUBAGENT_TASKS
from tests.agent.test_events_view import _body, _drain, _factory, _route, _server
from tests.agent.test_subagent_task_control import _call
from tests.agent.test_task_controls_view import _model, _said


async def test_a_run_stream_binds_the_sink_and_the_cards_together() -> None:
    """``_hand_over`` relies on this: it asks about the sink, never about the cards."""
    bound: list[tuple[bool, bool]] = []

    def look() -> None:
        bound.append((SUBAGENT_SINK.get() is not None, SUBAGENT_TASKS.get() is not None))

    async def upstream() -> AsyncIterator[BaseEvent]:
        look()
        yield RunStartedEvent(thread_id="t1", run_id="r1")

    async def consume() -> None:
        look()
        async for _ in inject_subagent_events(upstream()):
            pass
        look()

    await asyncio.ensure_future(consume())

    assert bound == [(False, False), (True, True), (False, False)]


@pytest.mark.django_db(transaction=True)
async def test_a_delegation_after_the_stream_ended_leaves_the_live_handle_alone() -> None:
    """``RunStore.finish`` runs with the live handle still bound and the sink gone."""
    delegate = _call("delegate_task", {"agent_name": "researcher", "task": "Go."}, "call-1")
    child = Agent(TestModel(), name="researcher", description="Looks phrases up.")
    sub_agents = SubAgents(agents=[SubAgent(child)], agent_folders=None, task_control=TaskControl())
    parent = Agent(
        _model(_said(delegate), _said("Summed up.")),
        capabilities=[SubAgentObserver(sub_agents)],
    )
    seen: list[Any] = []

    class Summarising(DefaultRunStore):
        async def finish(self, *args: Any, **kwargs: Any) -> Any:
            live = RUN_TASKS.get()
            assert live is not None
            result = await parent.run("Sum the run up.")
            seen.append((SUBAGENT_SINK.get(), result.output, await live.tasks()))
            return await super().finish(*args, **kwargs)

    server = _server(TestModel(), Summarising())
    request = _factory().post("/agent/", data=_body("r1"), content_type="application/json")
    await _drain(await _route(server, "endpoint")(request))

    assert seen == [(None, "Summed up.", [])]
