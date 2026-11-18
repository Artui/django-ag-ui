from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

from ag_ui.core import RunAgentInput, UserMessage
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django_spec_agents.headless.types.firing_queue import FiringQueue
from django_spec_agents.headless.types.headless_firing import HeadlessFiring
from django_spec_agents.headless.types.headless_start import HeadlessStart
from django_spec_agents.headless.types.run_stop import RunStop
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.utils import owner_id_for
from pydantic_ai.capabilities import AbstractCapability

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.constants import QUEUED_RUN_LEASE, RUN_HEADLESS_SPEC


class AGUIHeadlessRunner:
    """A [`HeadlessRunner`][django_spec_agents.HeadlessRunner] over this package's mounts.

    Each mount is keyed by its ``AGUIServer(id=)``, and is checked when the runner is
    built: a headless run needs the mount's ``run_store=``, its ``step_store=``, and a
    conversation store that persists without a browser session. A run takes
    ``timeout=`` and a principal that is a user; an anonymous one is refused, since
    its owner scope needs a browser session. The caller authorizes the run: the
    mount's ``get_user``, ``authorize`` and ``throttle`` are not consulted.

    The prompt is the run's only new message; the thread's stored conversation, loaded
    once the run holds the thread, is its history, and it carries no client tools. A
    ``parent`` and ``lineage`` are saved on the child's conversation before ``start`` returns,
    and every run of the thread, the first included, composes the bounds they describe.
    ``answers`` resumes a thread parked on a question with no new prompt. After a run's end
    is recorded, mail that arrived during a run that did not fail wakes its thread.

    With a ``queue``, ``start`` claims the thread for ``queued_lease``, then enqueues
    ``spec_name`` for a worker, whose runner takes the claim up by its ``run_id``.
    """

    def __init__(
        self,
        *servers: AGUIServer,
        queue: FiringQueue | None = None,
        spec_name: str = RUN_HEADLESS_SPEC,
        queued_lease: timedelta = QUEUED_RUN_LEASE,
    ) -> None:
        self._queue = queue
        self._spec_name = spec_name
        self._queued_lease = queued_lease
        self._servers: dict[str, AGUIServer] = {}
        for server in servers:
            server.check_headless()
            assert server.id is not None
            if server.id in self._servers:
                raise ImproperlyConfigured(f"two mounts share the id {server.id!r}")
            self._servers[server.id] = server

    @property
    def queued(self) -> bool:
        """Whether ``start`` enqueues the run for a worker rather than start it here."""
        return self._queue is not None

    async def start(
        self,
        agent_id: str,
        prompt: str,
        principal: Any,
        *,
        timeout: float,
        thread_id: str | None = None,
        parent: ThreadRef | None = None,
        lineage: ChildLineageRecord | None = None,
        capabilities: Sequence[AbstractCapability[Any]] = (),
        answers: Mapping[str, str] | None = None,
    ) -> HeadlessStart:
        """Start the run and return once it has claimed its thread; it goes on elsewhere."""
        if self._queue is not None:
            return await self._enqueue(
                self._queue,
                agent_id,
                prompt,
                principal,
                timeout,
                thread_id,
                parent,
                lineage,
                capabilities,
                answers,
            )
        server, request, run_input = self._prepare(
            agent_id, prompt, principal, timeout, thread_id, answers=answers
        )
        await server.start_headless(
            request,
            run_input,
            timeout=timeout,
            capabilities=capabilities,
            parent=parent,
            lineage=lineage,
            answers=answers,
            after_finish=await self._waker(server, request, run_input.thread_id, timeout, self),
        )
        return HeadlessStart(thread_id=run_input.thread_id, run_id=run_input.run_id)

    async def run_to_first_stop(
        self,
        agent_id: str,
        prompt: str,
        principal: Any,
        *,
        timeout: float,
        thread_id: str | None = None,
        parent: ThreadRef | None = None,
        lineage: ChildLineageRecord | None = None,
        capabilities: Sequence[AbstractCapability[Any]] = (),
        run_id: str | None = None,
        answers: Mapping[str, str] | None = None,
    ) -> RunStop:
        """Run until it ends; a ``run_id`` takes up the claim a queued ``start`` made."""
        server, request, run_input = self._prepare(
            agent_id, prompt, principal, timeout, thread_id, run_id, answers=answers
        )
        thread, run = run_input.thread_id, run_input.run_id
        if run_id is not None and not await server.take_up_queued(request, thread, run):
            return RunStop(thread_id=thread, run_id=run, outcome=RunOutcome.CANCELLED)
        try:
            kept = None if run_id is None else await server.queued_mark(request, run)
            waker = await self._waker(
                server, request, thread, timeout, _RunsToFirstStop(self), kept
            )
            stored = None if run_id is None else await server.queued_input(request, run)
            if stored is not None:
                owner = await server.start_queued(
                    request, stored, timeout=timeout, after_finish=waker
                )
            elif not prompt and not answers:
                raise LookupError(
                    f"run {run!r} has no prompt, no answers and no kept browser input"
                )
            else:
                owner = await server.start_headless(
                    request,
                    run_input,
                    timeout=timeout,
                    capabilities=capabilities,
                    parent=parent,
                    lineage=lineage,
                    adopt=run_id is not None,
                    answers=answers,
                    after_finish=waker,
                )
        except BaseException:
            await server.abandon_queued(request, thread, run)
            raise
        outcome = await owner.wait()
        return RunStop(thread_id=thread, run_id=run, outcome=outcome, waiting=owner.waiting)

    async def cancel(self, thread_id: str, principal: Any) -> bool:
        """Record a cancel for the thread's live run; ``False`` if no run holds it."""
        cancelled = False
        for server in self._servers.values():
            request = self._request(server, principal)
            cancelled = await server.request_cancel(thread_id, request=request) or cancelled
        return cancelled

    async def _enqueue(
        self,
        queue: FiringQueue,
        agent_id: str,
        prompt: str,
        principal: Any,
        timeout: float,
        thread_id: str | None,
        parent: ThreadRef | None,
        lineage: ChildLineageRecord | None,
        capabilities: Sequence[AbstractCapability[Any]],
        answers: Mapping[str, str] | None,
    ) -> HeadlessStart:
        if capabilities:
            raise ValueError(
                "a queued headless start carries no capabilities=: they cannot cross a queue"
            )
        server, request, run_input = self._prepare(
            agent_id, prompt, principal, timeout, thread_id, answers=answers
        )
        mark = await server.inbox_mark(request, run_input.thread_id)
        await server.claim_queued(
            request,
            run_input,
            lease=self._queued_lease,
            parent=parent,
            lineage=lineage,
            inbox_mark=mark,
        )
        firing = HeadlessFiring(
            agent_id,
            prompt,
            timeout,
            run_input.thread_id,
            parent,
            lineage,
            run_input.run_id,
            None if answers is None else dict(answers),
            mark,
        )
        params = dataclasses.asdict(firing)
        try:
            await sync_to_async(queue.enqueue)(self._spec_name, params, actor=principal)
        except BaseException:
            await server.abandon_queued(request, run_input.thread_id, run_input.run_id)
            raise
        return HeadlessStart(thread_id=run_input.thread_id, run_id=run_input.run_id)

    def _prepare(
        self,
        agent_id: str,
        prompt: str,
        principal: Any,
        timeout: float,
        thread_id: str | None,
        run_id: str | None = None,
        *,
        answers: Mapping[str, str] | None = None,
    ) -> tuple[AGUIServer, Any, RunAgentInput]:
        if not timeout > 0:
            raise ValueError(f"a headless run needs a positive timeout=, not {timeout!r}")
        server = self._servers.get(agent_id)
        if server is None:
            raise LookupError(f"no headless mount has the id {agent_id!r}")
        request = self._request(server, principal)
        run_input = RunAgentInput(
            thread_id=thread_id or uuid.uuid4().hex,
            run_id=run_id or uuid.uuid4().hex,
            state=None,
            messages=[] if answers else [UserMessage(id=uuid.uuid4().hex, content=prompt)],
            tools=[],
            context=[],
            forwarded_props=None,
        )
        return server, request, run_input

    @staticmethod
    async def _waker(
        server: AGUIServer,
        request: Any,
        thread_id: str,
        timeout: float,
        runner: Any,
        kept: int | None = None,
    ) -> Callable[[RunOutcome], Awaitable[None]] | None:
        """What wakes the thread after the run; mail is new past ``kept``, the enqueue's mark."""
        mark = await server.inbox_mark(request, thread_id)
        if mark is None:
            return None
        after = mark if kept is None else kept

        async def wake(outcome: RunOutcome) -> None:
            if outcome is not RunOutcome.ERROR:
                await server.wake(request, thread_id, after=after, runner=runner, timeout=timeout)

        return wake

    @staticmethod
    def _request(server: AGUIServer, principal: Any) -> Any:
        request = server.headless_request(principal)
        if owner_id_for(request) is None:
            raise PermissionDenied("a headless run needs an authenticated principal")
        return request


class _RunsToFirstStop:
    """Wakes by running to the first stop, for a run whose loop may end once it returns."""

    def __init__(self, runner: AGUIHeadlessRunner) -> None:
        self._runner = runner

    async def start(
        self, agent_id: str, prompt: str, principal: Any, **options: Any
    ) -> HeadlessStart:
        stop = await self._runner.run_to_first_stop(agent_id, prompt, principal, **options)
        return HeadlessStart(thread_id=stop.thread_id, run_id=stop.run_id)


__all__ = ["AGUIHeadlessRunner"]
