from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from ag_ui.core import BaseEvent

from django_ag_ui.agent.live_tasks import LiveTasks
from django_ag_ui.agent.task_control_refused import TaskControlRefused
from django_ag_ui.agent.types.subagent_delegation import SubAgentDelegation
from django_ag_ui.agent.utils import TASK_TEXT_LIMIT, task_text


class _Owner:
    """The ``DelegationTasks`` surface the handle drives, recording each call."""

    def __init__(self, **records: SimpleNamespace) -> None:
        self.records = records
        self.calls: list[tuple[Any, ...]] = []
        self.question: tuple[str, str] | None = None
        self.answers = True

    def steer(self, task_id: str, text: str) -> bool:
        self.calls.append(("steer", task_id, text))
        return True

    def pending_question(self, task_id: str) -> tuple[str, str] | None:
        return self.question

    def answer(self, task_id: str, *, question_id: str, answer: str) -> bool:
        self.calls.append(("answer", task_id, question_id, answer))
        return self.answers

    def request_stop(self, task_id: str) -> None:
        self.calls.append(("request_stop", task_id))

    async def cancel(self, task_id: str, *, user: bool) -> None:
        self.calls.append(("cancel", task_id, user))


def _record(**fields: Any) -> SimpleNamespace:
    base = {"parent_id": None, "status": "running", "agent_name": "researcher"}
    return SimpleNamespace(**{"background": True, **base, **fields})


def _live(owner: _Owner, *, remote: tuple[str, ...] = ()) -> tuple[LiveTasks, asyncio.Queue]:
    live = LiveTasks(asyncio.get_running_loop())
    sink: asyncio.Queue[BaseEvent] = asyncio.Queue()
    cards = {task_id: SubAgentDelegation("call-1", "researcher") for task_id in owner.records}
    live.attach(owner, sink=sink, cards=cards, remote=remote)
    return live, sink


def _steps(sink: asyncio.Queue) -> list[dict[str, Any]]:
    return [sink.get_nowait().value for _ in range(sink.qsize())]


async def _refusal(control: Any) -> tuple[int, str]:
    with pytest.raises(TaskControlRefused) as refused:
        await control
    return refused.value.status, refused.value.reason


def test_a_long_text_is_cut_and_marked() -> None:
    assert task_text("question", "short") == {"question": "short"}
    assert task_text("question", "x" * (TASK_TEXT_LIMIT + 1)) == {
        "question": "x" * TASK_TEXT_LIMIT,
        "truncated": True,
    }


async def test_a_steer_reaches_the_owner_and_reports_the_users_words() -> None:
    owner = _Owner(t1=_record())
    live, sink = _live(owner)

    await live.steer("t1", "Only 2024.")

    assert owner.calls == [("steer", "t1", "Only 2024.")]
    [step] = _steps(sink)
    assert step["phase"] == "steered"
    assert step["delegationId"] == "call-1"
    assert step["status"] == "researcher: steered by you"
    assert step["task"] == {"taskId": "t1", "message": "Only 2024.", "source": "user"}


async def test_an_answer_names_the_question_it_answers() -> None:
    owner = _Owner(t1=_record())
    owner.question = ("q1", "Which year?")
    live, sink = _live(owner)

    await live.answer("t1", "q1", "2024")

    assert owner.calls == [("answer", "t1", "q1", "2024")]
    assert _steps(sink)[0]["task"] == {
        "taskId": "t1",
        "questionId": "q1",
        "answer": "2024",
        "source": "user",
    }


@pytest.mark.parametrize(
    ("question", "answers"),
    [(None, True), (("q2", "Which month?"), True), (("q1", "Which year?"), False)],
)
async def test_an_answer_to_a_question_not_waiting_is_refused(
    question: tuple[str, str] | None, answers: bool
) -> None:
    owner = _Owner(t1=_record())
    owner.question, owner.answers = question, answers
    live, sink = _live(owner)

    assert await _refusal(live.answer("t1", "q1", "2024")) == (409, "not_waiting")
    assert sink.empty()


@pytest.mark.parametrize(("force", "call"), [(False, ("request_stop", "t1")), (True, None)])
async def test_a_cancel_asks_softly_or_forces(force: bool, call: Any) -> None:
    owner = _Owner(t1=_record())
    live, sink = _live(owner)

    await live.cancel("t1", force=force)

    assert owner.calls == [call or ("cancel", "t1", True)]
    [step] = _steps(sink)
    assert step["phase"] == "cancel_requested"
    assert step["task"] == {"taskId": "t1", "forced": force, "source": "user"}


async def test_the_list_holds_running_carded_tasks_with_their_question() -> None:
    owner = _Owner(
        t1=_record(),
        t2=_record(status="finished"),
        t3=_record(parent_id="t1"),
        t4=_record(agent_name="far", background=False),
    )
    owner.question = ("q1", "x" * (TASK_TEXT_LIMIT + 5))
    live, _ = _live(owner, remote=("far",))

    listed = await live.tasks()

    assert listed == [
        {
            "task_id": "t1",
            "delegation_id": "call-1",
            "agent": "researcher",
            "status": "running",
            "background": True,
            "question": {"question_id": "q1", "question": "x" * TASK_TEXT_LIMIT, "truncated": True},
        }
    ]
    owner.question = None
    assert [task["question"] for task in await live.tasks()] == [None]


async def test_a_task_that_cannot_be_controlled_is_refused_with_its_reason() -> None:
    owner = _Owner(t1=_record(status="finished"), t2=_record(parent_id="t1"), t3=_record())
    owner.records["far"] = _record(agent_name="far")
    live, _ = _live(owner, remote=("far",))

    assert await _refusal(live.steer("missing", "x")) == (404, "no_task")
    assert await _refusal(live.steer("t2", "x")) == (404, "no_task")
    assert await _refusal(live.steer("t1", "x")) == (409, "not_running")
    assert await _refusal(live.cancel("far", force=False)) == (409, "remote")
    assert owner.calls == []


async def test_a_settled_tasks_card_is_gone_and_it_reads_as_not_running() -> None:
    owner = _Owner(t1=_record())
    live, _ = _live(owner)
    owner.records["t9"] = _record()

    assert await _refusal(live.steer("t9", "x")) == (409, "not_running")


async def test_a_handle_nobody_attached_has_no_tasks() -> None:
    live = LiveTasks(asyncio.get_running_loop())

    assert await live.tasks() == []
    assert await _refusal(live.steer("t1", "x")) == (404, "no_task")


async def test_only_the_first_attach_is_kept() -> None:
    first, second = _Owner(t1=_record()), _Owner(t1=_record())
    live, _ = _live(first)
    live.attach(second, sink=asyncio.Queue(), cards={}, remote=())

    await live.steer("t1", "x")

    assert (first.calls, second.calls) == ([("steer", "t1", "x")], [])


async def test_a_control_from_another_loop_runs_on_the_runs_loop() -> None:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    owner = _Owner(t1=_record())
    seen: list[asyncio.AbstractEventLoop] = []
    owner.steer = lambda task_id, text: seen.append(asyncio.get_running_loop()) or True  # ty: ignore[invalid-assignment]
    live = LiveTasks(loop)
    cards = {"t1": SubAgentDelegation("call-1", "researcher")}
    live.attach(owner, sink=asyncio.Queue(), cards=cards, remote=())
    try:
        await live.steer("t1", "x")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()

    assert seen == [loop]
    assert await _refusal(live.steer("t1", "x")) == (404, "not_live_here")
