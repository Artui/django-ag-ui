from __future__ import annotations

from typing import Any

import pytest
from django_spec_agents.child_sessions.report_frame import report_frame
from django_spec_agents.child_sessions.types.message_origin import MessageOrigin
from django_spec_agents.child_sessions.types.message_origin_key import MESSAGE_ORIGIN_KEY
from django_spec_agents.child_sessions.types.message_task_key import MESSAGE_TASK_KEY
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, TextContent, UserPromptPart

from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.agent.outcome_agui_adapter import OutcomeAGUIAdapter
from django_ag_ui.agent.prompt_metadata import with_prompt_metadata
from tests.agent.test_enqueued_deliveries_are_stored_as_streamed import (
    _deliveries,
    _nudging_agent,
)
from tests.agent.test_headless_runner import _Model, _server, _settled
from tests.agent.test_stored_ids_match_the_stream import _new, _run, _session, _Store
from tests.agent.worker_queue import WorkerQueue
from tests.authed_request_factory import authenticated_user

WAKE = {MESSAGE_ORIGIN_KEY: MessageOrigin.WAKE.value}
CHILD = ThreadRef("child-1", "helper")


def _question() -> UserPromptPart:
    mark = {
        MESSAGE_ORIGIN_KEY: MessageOrigin.TASK_QUESTION.value,
        MESSAGE_TASK_KEY: {"taskId": "child-1", "questionId": "q1", "body": "which one?"},
    }
    return UserPromptPart([TextContent("<task_question>which one?</task_question>", metadata=mark)])


def _marks(messages: list[dict[str, Any]]) -> list[Any]:
    return [(m.get("metadata") or {}).get(MESSAGE_ORIGIN_KEY) for m in messages]


async def _stored(server: Any, thread_id: str, user: Any) -> list[dict[str, Any]]:
    request = server.headless_request(user)
    conversation = await DefaultConversationStore().load(thread_id, request=request)
    return conversation.messages


@pytest.mark.django_db(transaction=True)
async def test_a_wake_line_is_stored_with_its_mark_and_the_next_save_keeps_it() -> None:
    server, user = _server(), authenticated_user()
    runner = AGUIHeadlessRunner(server)

    woken = await runner.run_to_first_stop(
        "helper", "messages are waiting", user, timeout=5, prompt_metadata=WAKE
    )
    await runner.run_to_first_stop("helper", "mine", user, timeout=5, thread_id=woken.thread_id)

    stored = await _stored(server, woken.thread_id, user)
    assert [m["content"] for m in stored] == [
        "messages are waiting",
        "answer 1",
        "mine",
        "answer 2",
    ]
    assert _marks(stored) == ["wake", None, None, None]


@pytest.mark.django_db(transaction=True)
async def test_a_background_start_stores_the_wake_mark_too() -> None:
    model = _Model()
    server, user = _server(model), authenticated_user()

    start = await AGUIHeadlessRunner(server).start(
        "helper", "messages are waiting", user, timeout=5, prompt_metadata=WAKE
    )
    await _settled(server, start.thread_id)

    assert _marks(await _stored(server, start.thread_id, user))[0] == "wake"


@pytest.mark.django_db(transaction=True)
async def test_a_queued_wake_keeps_its_mark_through_the_worker() -> None:
    server, user = _server(), authenticated_user()
    queue = WorkerQueue(AGUIHeadlessRunner(server))

    start = await AGUIHeadlessRunner(server, queue=queue).start(
        "helper", "messages are waiting", user, timeout=5, prompt_metadata=WAKE
    )
    await queue.work()

    assert queue.enqueued[0][1]["prompt_metadata"] == WAKE
    assert _marks(await _stored(server, start.thread_id, user)) == ["wake", None]


async def test_each_enqueued_part_carries_its_mark_streamed_and_stored() -> None:
    report = report_frame(CHILD, agent_id="helper", output="all done")

    def nudge(ctx: RunContext[Any]) -> str:
        ctx.enqueue(report, _question(), "the user's own words")
        return "ok"

    store = _Store()

    frames = await _run(_session(_nudging_agent(nudge), store))

    (delivery,) = _deliveries(frames)
    entries = delivery["content"]["messages"]
    assert _marks(entries) == ["task_output", "task_question", None]
    assert entries[0]["metadata"][MESSAGE_TASK_KEY]["body"] == "all done"
    assert entries[0]["content"] == report.content[0].content
    (activity,) = [m for m in _new(store.saved[0], after=1) if m["role"] == "activity"]
    assert activity["content"] == delivery["content"]


def test_replay_keeps_each_parts_mark() -> None:
    report = report_frame(CHILD, agent_id="helper", output="all done", outcome="failed")
    history = [ModelRequest(parts=[UserPromptPart("hi"), report, _question()])]

    dumped = OutcomeAGUIAdapter.dump_messages(history)

    assert [(m.metadata or {}).get(MESSAGE_ORIGIN_KEY) for m in dumped] == [
        None,
        "task_output",
        "task_question",
    ]
    assert dumped[1].metadata[MESSAGE_TASK_KEY]["outcome"] == "failed"


def test_a_dump_whose_user_messages_do_not_line_up_is_left_unmarked() -> None:
    history = [ModelRequest(parts=[report_frame(CHILD, agent_id="helper", output="x")])]
    dumped = OutcomeAGUIAdapter.dump_messages(history)
    extra = [*dumped, *dumped]

    assert with_prompt_metadata(history, extra) == extra
