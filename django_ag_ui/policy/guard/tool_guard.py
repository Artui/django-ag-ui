"""``ToolGuard`` — flip destructive tools to require human-in-the-loop approval."""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition

from django_ag_ui.constants import DESTRUCTIVE_METADATA_KEY
from django_ag_ui.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_ag_ui.registry.tool_registry import ToolRegistry


class ToolGuard(AbstractCapability[Any]):
    """Gates destructive server-side tools behind the AG-UI approval loop.

    pydantic-ai supplies the *mechanism* — a tool whose definition is
    ``kind="unapproved"`` defers to a ``RUN_FINISHED`` interrupt the client
    approves or denies (see the HITL wave). ``ToolGuard`` supplies the
    *policy*: at ``prepare_tools`` time it flips a plain ``function`` tool to
    ``unapproved`` when the tool is destructive, so **server-side** tools get
    the same confirmation gate the web component already applies to
    client-registered destructive tools.

    Destructiveness is read from three sources, unified here so one hook covers
    every tool the agent sees regardless of where it came from:

    - **Registry** ``@tool(destructive=True)`` — its name is collected from the
      registry at construction (the ``destructive`` flag lives on the spec and
      never reaches pydantic-ai as a bare callable, so the capability reads it
      directly).
    - **drf-mcp bridged tools** — the bridge stamps
      :data:`~django_ag_ui.constants.DESTRUCTIVE_METADATA_KEY` into
      ``ToolDefinition.metadata`` for any tool whose MCP ``readOnlyHint`` is
      ``False``; the guard reads that metadata.
    - **Project overrides** — ``require_approval`` force-gates a name;
      ``exempt`` un-gates one (``exempt`` wins).

    Only ``kind="function"`` tools are flipped: an ``external`` (frontend) tool
    is already gated client-side, and an ``output`` tool isn't executed.

    Ordering: the guard touches only the ``prepare_tools`` hook, so it is
    orthogonal to :class:`~django_ag_ui.policy.audit.audit_capability.AuditCapability`
    (which wraps execution) — audit's ``get_ordering`` pins it outermost, so
    audit still records the tool when an approved call finally runs.
    """

    def __init__(self, registry: ToolRegistry, *, config: ToolGuardConfig) -> None:
        self._destructive_names = frozenset(
            binding.spec.name for binding in registry if binding.spec.destructive
        )
        self._exempt = config.exempt
        self._require_approval = config.require_approval

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        return [
            dataclasses.replace(tool_def, kind="unapproved")
            if tool_def.kind == "function" and self._requires_approval(tool_def)
            else tool_def
            for tool_def in tool_defs
        ]

    def _requires_approval(self, tool_def: ToolDefinition) -> bool:
        """Whether ``tool_def`` should be gated, applying the policy precedence."""
        if tool_def.name in self._exempt:
            return False
        if tool_def.name in self._require_approval:
            return True
        if tool_def.name in self._destructive_names:
            return True
        metadata = tool_def.metadata or {}
        return bool(metadata.get(DESTRUCTIVE_METADATA_KEY))


__all__ = ["ToolGuard"]
