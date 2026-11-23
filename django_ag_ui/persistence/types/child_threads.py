from __future__ import annotations

from typing import Any, Protocol

from django_spec_agents.child_sessions.types.child_session import ChildSession


class ChildThreads(Protocol):
    """Answers for a thread's child sessions across every mount; ``AGUIHeadlessRunner`` is one."""

    async def children(self, agent_id: str, thread_id: str, principal: Any) -> list[ChildSession]:
        """The principal's child sessions the thread started on the mount ``agent_id``."""
        ...

    async def delete_thread(self, agent_id: str, thread_id: str, principal: Any) -> None:
        """Cancel the thread's live descendants, unlink its children, then delete it."""
        ...


__all__ = ["ChildThreads"]
