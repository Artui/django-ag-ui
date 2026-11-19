from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from django_spec_agents import ToolGuard, build_run_waiting
from django_spec_agents.persistence.types.run_waiting import RunWaiting
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import DeferredToolRequests, ToolDefinition


class ParkRun(AbstractCapability[Any]):
    """Keeps one run's tool definitions, to say what the run waits on if it ends deferred.

    ``client_tools`` are the browser's own, whose calls a connected client answers itself.
    """

    def __init__(
        self,
        *,
        guard: ToolGuard,
        prompts: Mapping[str, str],
        client_tools: Collection[str] = (),
    ) -> None:
        self._guard = guard
        self._prompts = prompts
        self._client_tools = frozenset(client_tools)
        self._defs: dict[str, ToolDefinition] = {}

    async def prepare_tools(
        self, ctx: RunContext[Any], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        self._defs.update((tool_def.name, tool_def) for tool_def in tool_defs)
        return tool_defs

    def waiting(self, output: Any) -> RunWaiting | None:
        """The first deferred call the run parks on, approvals first; ``None`` if it does not."""
        if not isinstance(output, DeferredToolRequests):
            return None
        calls = [call for call in output.calls if call.tool_name not in self._client_tools]
        pending = [*output.approvals, *calls]
        if not pending:
            return None
        call = pending[0]
        name = call.tool_name
        tool_def = self._defs.get(name, ToolDefinition(name=name))
        return build_run_waiting(
            output,
            call.tool_call_id,
            summary=self._prompts.get(name) or name,
            destructive=self._guard.requires_approval(tool_def),
        )


__all__ = ["ParkRun"]
