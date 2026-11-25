from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Coroutine, Mapping
from contextvars import ContextVar
from typing import Any, TypeVar

from ag_ui.core import BaseEvent

from django_ag_ui.agent.subagent_progress import subagent_progress
from django_ag_ui.agent.task_control_refused import TaskControlRefused
from django_ag_ui.agent.types.subagent_delegation import SubAgentDelegation
from django_ag_ui.agent.utils import task_text

_T = TypeVar("_T")


class LiveTasks:
    """One live run's sub-agent task control, handed over by its observer, driven by a route.

    Every control runs on the run's own loop: the harness's question futures belong to it."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._owner: Any = None
        self._sink: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._cards: Mapping[str, SubAgentDelegation] = {}
        self._remote: frozenset[str] = frozenset()

    def attach(
        self,
        owner: Any,
        *,
        sink: asyncio.Queue[BaseEvent],
        cards: Mapping[str, SubAgentDelegation],
        remote: Collection[str],
    ) -> None:
        """Keep the run's ``DelegationTasks``, its sub-agent sink and carded tasks; first wins."""
        if self._owner is None:
            self._owner, self._sink, self._cards = owner, sink, cards
            self._remote = frozenset(remote)

    async def tasks(self) -> list[dict[str, Any]]:
        """Every carded task still running, with the question it waits on, if any."""
        return await self._on_loop(self._tasks)

    async def steer(self, task_id: str, message: str) -> None:
        """Send the running task ``message``, read at its next model request."""
        await self._on_loop(lambda: self._steer(task_id, message))

    async def answer(self, task_id: str, question_id: str, answer: str) -> None:
        """Answer the question ``question_id`` the task waits on."""
        await self._on_loop(lambda: self._answer(task_id, question_id, answer))

    async def cancel(self, task_id: str, *, force: bool) -> None:
        """Stop the task before its next model request, or at once with ``force``."""
        await self._on_loop(lambda: self._cancel(task_id, force))

    async def _tasks(self) -> list[dict[str, Any]]:
        listed = []
        for task_id in list(self._cards):
            try:
                record, delegation = self._card(task_id)
            except TaskControlRefused:
                continue
            pending = self._owner.pending_question(task_id)
            question = None
            if pending is not None:
                question = {"question_id": pending[0], **task_text("question", pending[1])}
            listed.append(
                {
                    "task_id": task_id,
                    "delegation_id": delegation.delegation_id,
                    "agent": delegation.agent,
                    "status": record.status,
                    "background": record.background,
                    "question": question,
                }
            )
        return listed

    async def _steer(self, task_id: str, message: str) -> None:
        delegation = self._card(task_id)[1]
        self._owner.steer(task_id, message)
        self._report(delegation, task_id, "steered", "steered by you", message=message)

    async def _answer(self, task_id: str, question_id: str, answer: str) -> None:
        delegation = self._card(task_id)[1]
        pending = self._owner.pending_question(task_id)
        if (
            pending is None
            or pending[0] != question_id
            or not self._owner.answer(task_id, question_id=question_id, answer=answer)
        ):
            raise TaskControlRefused(409, "not_waiting", "the task is not waiting on that question")
        self._report(
            delegation,
            task_id,
            "answered",
            "answered by you",
            questionId=question_id,
            answer=answer,
        )

    async def _cancel(self, task_id: str, force: bool) -> None:
        delegation = self._card(task_id)[1]
        said = "cancellation requested by you"
        self._report(delegation, task_id, "cancel_requested", said, forced=force)
        if force:
            await self._owner.cancel(task_id, user=True)
        else:
            self._owner.request_stop(task_id)

    def _card(self, task_id: str) -> tuple[Any, SubAgentDelegation]:
        """The task's record and card, or the refusal a route answers with."""
        record = None if self._owner is None else self._owner.records.get(task_id)
        delegation = self._cards.get(task_id)
        if record is None or record.parent_id is not None:
            raise TaskControlRefused(404, "no_task", "the run has no task by that id")
        if record.status != "running" or delegation is None:
            raise TaskControlRefused(409, "not_running", "the task is not running")
        if record.agent_name in self._remote:
            raise TaskControlRefused(409, "remote", "the task runs on a remote worker")
        return record, delegation

    def _report(
        self, delegation: SubAgentDelegation, task_id: str, phase: Any, said: str, **task: Any
    ) -> None:
        step = subagent_progress(
            delegation_id=delegation.delegation_id,
            agent=delegation.agent,
            phase=phase,
            status=f"{delegation.agent}: {said}",
            task={"taskId": task_id, **task, "source": "user"},
        )
        self._sink.put_nowait(step)

    async def _on_loop(self, control: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
        """``control`` awaited on the run's loop, from whichever loop the route runs on."""
        if asyncio.get_running_loop() is self.loop:
            return await control()
        work = control()
        try:
            future = asyncio.run_coroutine_threadsafe(work, self.loop)
        except RuntimeError:
            work.close()
            raise TaskControlRefused(404, "not_live_here", "the run has ended") from None
        return await asyncio.wrap_future(future)


RUN_TASKS: ContextVar[LiveTasks | None] = ContextVar("django_ag_ui_run_tasks", default=None)


__all__ = ["RUN_TASKS", "LiveTasks"]
