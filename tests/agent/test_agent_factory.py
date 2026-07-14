from __future__ import annotations

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.policy.audit.types.audit_event import AuditEvent
from django_ag_ui.registry.decorator import tool
from django_ag_ui.registry.tool_registry import ToolRegistry


class _CapturingLogger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_build_agent_returns_agent_with_registry_tools() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    agent = build_agent(reg, AgentConfig(model=TestModel()))
    assert isinstance(agent, Agent)


def test_build_agent_puts_deferred_tool_requests_in_output_type() -> None:
    # Turns on the tool-approval interrupt loop for *server-side* tools. The
    # AG-UI adapter only augments ``output_type`` with ``DeferredToolRequests``
    # when the run carries frontend tools, so a server-only gated tool would
    # otherwise crash the run with a RUN_ERROR ("a deferred tool call was
    # present, but DeferredToolRequests is not among output types"). Setting it
    # on the Agent makes the approval path independent of frontend tools.
    agent = build_agent(ToolRegistry(), AgentConfig(model=TestModel()))
    assert DeferredToolRequests in agent.output_type


def test_build_agent_accepts_model_settings_retries_toolsets_capabilities() -> None:
    from pydantic_ai.toolsets import FunctionToolset

    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    agent = build_agent(
        reg,
        AgentConfig(
            model=TestModel(),
            model_settings={"temperature": 0.1},
            retries=2,
            toolsets=[FunctionToolset()],
            capabilities=[object()],
        ),
    )
    assert isinstance(agent, Agent)


async def test_audited_sync_tool_records_success() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    await agent.run("double 3")

    assert audit.events, "expected at least one audited call"
    event = audit.events[0]
    assert event.tool_name == "double"
    assert event.success is True
    assert event.result_size is not None


async def test_audited_sync_tool_records_failure() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def boom(n: int) -> int:
        """Always explodes."""
        raise ValueError("kaboom")

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    with pytest.raises(ValueError, match="kaboom"):
        await agent.run("call boom with 1")

    failures = [e for e in audit.events if not e.success]
    assert failures
    assert "kaboom" in (failures[0].error or "")


async def test_audited_async_tool_records_success() -> None:
    reg = ToolRegistry()

    @tool(reg)
    async def afetch(label: str) -> str:
        """Fetch something asynchronously."""
        return f"value:{label}"

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    await agent.run("afetch x")

    successes = [e for e in audit.events if e.success and e.tool_name == "afetch"]
    assert successes


async def test_audited_async_tool_records_failure() -> None:
    reg = ToolRegistry()

    @tool(reg)
    async def aboom(label: str) -> str:
        """Async explosion."""
        raise RuntimeError("async kaboom")

    audit = _CapturingLogger()
    agent = build_agent(reg, AgentConfig(model=TestModel(), audit_logger=audit))
    with pytest.raises(RuntimeError, match="async kaboom"):
        await agent.run("aboom x")

    failures = [e for e in audit.events if not e.success and e.tool_name == "aboom"]
    assert failures
    assert "async kaboom" in (failures[0].error or "")


async def test_no_audit_logger_is_a_noop_default() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def double(n: int) -> int:
        """Double a number."""
        return n * 2

    # No audit_logger → NullAuditLogger; run must still succeed.
    agent = build_agent(reg, AgentConfig(model=TestModel()))
    result = await agent.run("double 4")
    assert result.output is not None
