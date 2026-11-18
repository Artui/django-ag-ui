from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from ag_ui.core import RunAgentInput, UserMessage
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django_spec_agents.headless.types.firing_queue import FiringQueue
from django_spec_agents.headless.types.headless_start import HeadlessStart
from django_spec_agents.headless.types.run_stop import RunStop
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.persistence.utils import owner_id_for
from pydantic_ai.capabilities import AbstractCapability

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.queued_start import RUN_HEADLESS_SPEC, enqueue_headless
from django_ag_ui.agent.run_owner import RunOwner


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
    ``parent`` and ``lineage`` are saved on the child's conversation before ``start`` returns.
    """

    def __init__(
        self,
        *servers: AGUIServer,
        queue: FiringQueue | None = None,
        spec_name: str = RUN_HEADLESS_SPEC,
    ) -> None:
        self._queue = queue
        self._spec_name = spec_name
        self._servers: dict[str, AGUIServer] = {}
        for server in servers:
            server.check_headless()
            assert server.id is not None
            if server.id in self._servers:
                raise ImproperlyConfigured(f"two mounts share the id {server.id!r}")
            self._servers[server.id] = server

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
    ) -> HeadlessStart:
        """Start the run and return once it has claimed its thread; queued, once enqueued."""
        if self._queue is not None:
            return enqueue_headless(
                self._queue,
                agent_id,
                prompt,
                principal,
                timeout=timeout,
                thread_id=thread_id,
                parent=parent,
                lineage=lineage,
                capabilities=capabilities,
                spec_name=self._spec_name,
            )
        thread, run, _ = await self._start(
            agent_id, prompt, principal, timeout, thread_id, parent, lineage, capabilities
        )
        return HeadlessStart(thread_id=thread, run_id=run)

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
    ) -> RunStop:
        """Run until the run ends, for a caller that waits on the answer."""
        thread, run, owner = await self._start(
            agent_id, prompt, principal, timeout, thread_id, parent, lineage, capabilities
        )
        return RunStop(thread_id=thread, run_id=run, outcome=await owner.wait())

    async def cancel(self, thread_id: str, principal: Any) -> bool:
        """Record a cancel for the thread's live run; ``False`` if no run holds it."""
        cancelled = False
        for server in self._servers.values():
            request = self._request(server, principal)
            cancelled = await server.request_cancel(thread_id, request=request) or cancelled
        return cancelled

    async def _start(
        self,
        agent_id: str,
        prompt: str,
        principal: Any,
        timeout: float,
        thread_id: str | None,
        parent: ThreadRef | None,
        lineage: ChildLineageRecord | None,
        capabilities: Sequence[AbstractCapability[Any]],
    ) -> tuple[str, str, RunOwner]:
        if not timeout > 0:
            raise ValueError(f"a headless run needs a positive timeout=, not {timeout!r}")
        server = self._servers.get(agent_id)
        if server is None:
            raise LookupError(f"no headless mount has the id {agent_id!r}")
        request = self._request(server, principal)
        thread_id = thread_id or uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        run_input = RunAgentInput(
            thread_id=thread_id,
            run_id=run_id,
            state=None,
            messages=[UserMessage(id=uuid.uuid4().hex, content=prompt)],
            tools=[],
            context=[],
            forwarded_props=None,
        )
        owner = await server.start_headless(
            request,
            run_input,
            timeout=timeout,
            capabilities=capabilities,
            parent=parent,
            lineage=lineage,
        )
        return thread_id, run_id, owner

    @staticmethod
    def _request(server: AGUIServer, principal: Any) -> Any:
        request = server.headless_request(principal)
        if owner_id_for(request) is None:
            raise PermissionDenied("a headless run needs an authenticated principal")
        return request


__all__ = ["AGUIHeadlessRunner"]
