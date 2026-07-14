"""``AuditCapability`` — audit every tool execution through one lifecycle hook."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    CapabilityOrdering,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from django_ag_ui.policy.audit.types.audit_event import AuditEvent
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger

_fallback_logger = logging.getLogger("django_ag_ui.audit")


class AuditCapability(AbstractCapability[Any]):
    """Records every tool execution to an :class:`AuditLogger` sink.

    A Pydantic-AI capability on the ``wrap_tool_execute`` lifecycle hook, so it
    times and records **every** tool the agent runs — registry tools, drf-mcp /
    spec-toolset bridges, attachment and skill tools alike — where the old
    per-tool wrapper saw only the registry.

    Recording is **non-raising**: a sink that throws is caught and logged to the
    ``django_ag_ui.audit`` Python logger, so a broken audit backend degrades to
    lost audit records, never a broken agent run.

    ``ip_address`` / ``organization_id`` pre-fill the matching
    :class:`AuditEvent` fields for every event this capability records — the
    view passes the client IP; a multi-tenant host can pass its org scope.
    """

    def __init__(
        self,
        logger: AuditLogger,
        *,
        ip_address: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        self._logger = logger
        self._ip_address = ip_address
        self._organization_id = organization_id

    def get_ordering(self) -> CapabilityOrdering:
        """Pin audit as the **outermost** capability in the chain.

        Audit is the observability layer: its ``wrap_tool_execute`` should
        surround every other capability's execution hooks so it records the tool
        regardless of what else composes the run. Declaring the position here
        (rather than relying on list order at the ``build_agent`` call site)
        makes composition deterministic once a second capability — e.g.
        :class:`~django_ag_ui.policy.guard.tool_guard.ToolGuard` — joins the
        chain: pydantic-ai's ``CombinedCapability`` topologically sorts by these
        constraints, so audit stays outermost no matter the insertion order.
        """
        return CapabilityOrdering(position="outermost")

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        started = time.perf_counter()
        try:
            result = await handler(args)
        except Exception as error:
            self._record(
                tool_def.name,
                args,
                started,
                success=False,
                error=f"{type(error).__name__}: {error}",
            )
            raise
        self._record(tool_def.name, args, started, success=True, result_size=len(str(result)))
        return result

    def _record(
        self,
        name: str,
        args: dict[str, Any],
        started: float,
        *,
        success: bool,
        error: str | None = None,
        result_size: int | None = None,
    ) -> None:
        event = AuditEvent(
            tool_name=name,
            arguments_repr=json.dumps(args, default=str, sort_keys=True),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            success=success,
            error=error,
            result_size=result_size,
            organization_id=self._organization_id,
            ip_address=self._ip_address,
        )
        try:
            self._logger.record(event)
        except Exception:
            _fallback_logger.exception(
                "audit logger %r raised while recording %r; event dropped",
                type(self._logger).__name__,
                name,
            )


__all__ = ["AuditCapability"]
