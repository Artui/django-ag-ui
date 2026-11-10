from __future__ import annotations

import asyncio
from typing import Any

import pytest
from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    SubagentErrorEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
)
from django.core.exceptions import ImproperlyConfigured
from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.subagents import SubAgent, SubAgents

from django_ag_ui import SubAgentObserver
from django_ag_ui.agent.subagent_observer import SUBAGENT_SINK


def _subagents(**kwargs: Any) -> SubAgents:
    """A real ``SubAgents``, with disk discovery off."""
    child = Agent(
        FunctionModel(lambda m, i: ModelResponse(parts=[TextPart(content="ok")])),
        name="researcher",
        description="Researches topics.",
    )
    kwargs.setdefault("agents", [SubAgent(child)])
    kwargs.setdefault("agent_folders", None)
    return SubAgents(**kwargs)


class _Call:
    """The slice of ``ToolCallPart`` the observer reads off a parent tool call."""

    def __init__(self, tool_name: str, tool_call_id: str = "call-1") -> None:
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id


@pytest.fixture
def sink() -> Any:
    queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
    token = SUBAGENT_SINK.set(queue)
    yield queue
    SUBAGENT_SINK.reset(token)


def _drain(queue: asyncio.Queue[BaseEvent]) -> list[BaseEvent]:
    """Everything announced, on both carriers, in the order it was queued."""
    events: list[BaseEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _step_values(queue: asyncio.Queue[BaseEvent]) -> list[dict[str, Any]]:
    """Just the ``CUSTOM`` step payloads -- what the child's tool calls ride."""
    return [event.value for event in _drain(queue) if isinstance(event, CustomEvent)]


async def _handler(_args: dict[str, Any]) -> str:
    return "child answer"


async def _events(*items: Any) -> Any:
    for item in items:
        yield item


class TestInstallation:
    def test_the_wrapped_capability_stays_reachable(self) -> None:
        # WrapperCapability delegates the rest of the protocol; losing that drops ordering silently.
        capability = _subagents()

        assert SubAgentObserver(capability).wrapped is capability

    def test_it_installs_the_handler_it_needs(self) -> None:
        # Child tool calls need SubAgents.event_stream_handler, which only the starter can pass.
        capability = _subagents()
        SubAgentObserver(capability)

        assert capability.event_stream_handler is not None

    def test_a_capability_that_cannot_be_observed_is_refused(self) -> None:
        # Reporting only start and end looks like it works, so it fails at construction.
        with pytest.raises(ImproperlyConfigured, match="no event_stream_handler"):
            SubAgentObserver(object())

    def test_an_existing_handler_is_not_silently_replaced(self) -> None:
        async def mine(ctx: Any, events: Any) -> None: ...  # pragma: no cover

        with pytest.raises(ImproperlyConfigured, match="already set"):
            SubAgentObserver(_subagents(event_stream_handler=mine))

    def test_a_renamed_delegate_tool_is_still_observed(self) -> None:
        # The name is configurable upstream; reading it late is what observes a renamed tool.
        observer = SubAgentObserver(_subagents(tool_name="ask_specialist"))

        assert observer._delegate_tool_name == "ask_specialist"


class TestObservingTheDelegation:
    async def test_a_delegation_is_announced_from_start_to_finish(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        observer = SubAgentObserver(_subagents())

        result = await observer.wrap_tool_execute(
            None,
            call=_Call("delegate_task"),
            tool_def=None,
            args={"agent_name": "researcher", "task": "look it up"},
            handler=_handler,
        )

        assert result == "child answer"
        # The protocol's own lifecycle, not this package's CUSTOM convention.
        opened, closed = _drain(sink)
        assert isinstance(opened, SubagentStartedEvent)
        assert (opened.name, opened.parent_tool_call_id) == ("researcher", "call-1")
        assert isinstance(closed, SubagentFinishedEvent)
        assert closed.subagent_run_id == opened.subagent_run_id

    async def test_a_failing_delegation_is_announced_and_still_raises(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        async def boom(_args: dict[str, Any]) -> str:
            raise RuntimeError("connection to db-prod-3 refused for user agent_svc")

        observer = SubAgentObserver(_subagents())

        with pytest.raises(RuntimeError):
            await observer.wrap_tool_execute(
                None,
                call=_Call("delegate_task"),
                tool_def=None,
                args={"agent_name": "auditor"},
                handler=boom,
            )

        opened, closed = _drain(sink)
        assert isinstance(opened, SubagentStartedEvent)
        assert isinstance(closed, SubagentErrorEvent)
        assert closed.subagent_run_id == opened.subagent_run_id
        # The operator's words stay with the operator.
        assert closed.message == "auditor failed"
        assert "db-prod-3" not in str([opened, closed])

    async def test_a_cancelled_delegation_is_still_closed(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        """The reversal, and the reason it matters more than a missing line."""

        async def cancelled(_args: dict[str, Any]) -> str:
            raise asyncio.CancelledError

        observer = SubAgentObserver(_subagents())

        with pytest.raises(asyncio.CancelledError):
            await observer.wrap_tool_execute(
                None,
                call=_Call("delegate_task"),
                tool_def=None,
                args={"agent_name": "researcher"},
                handler=cancelled,
            )

        opened, closed = _drain(sink)
        assert isinstance(opened, SubagentStartedEvent)
        assert isinstance(closed, SubagentErrorEvent)
        assert closed.subagent_run_id == opened.subagent_run_id

    async def test_another_tool_is_delegated_untouched_and_announces_nothing(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        # The hook fires for every tool in the parent run, not only the wrapped capability's.
        observer = SubAgentObserver(_subagents())

        result = await observer.wrap_tool_execute(
            None,
            call=_Call("send_invoice"),
            tool_def=None,
            args={"amount": 5},
            handler=_handler,
        )

        assert result == "child answer"
        assert _drain(sink) == []

    async def test_without_a_sink_the_observer_is_inert(self) -> None:
        # Outside this transport there is no stream; announcing must not blow up or leak.
        assert SUBAGENT_SINK.get() is None
        observer = SubAgentObserver(_subagents())

        result = await observer.wrap_tool_execute(
            None,
            call=_Call("delegate_task"),
            tool_def=None,
            args={"agent_name": "researcher"},
            handler=_handler,
        )

        assert result == "child answer"


class TestObservingTheChild:
    async def _forward(
        self, observer: SubAgentObserver, sink: asyncio.Queue[CustomEvent], *events: Any
    ) -> list[dict[str, Any]]:
        """Run one delegation whose child emits ``events``, and take what it said."""

        async def handler(_args: dict[str, Any]) -> str:
            await observer._forward_child_events(None, _events(*events))
            return "child answer"

        await observer.wrap_tool_execute(
            None,
            call=_Call("delegate_task"),
            tool_def=None,
            args={"agent_name": "researcher"},
            handler=handler,
        )
        return _step_values(sink)

    async def test_a_child_tool_call_is_reported_with_no_outcome_yet(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        observer = SubAgentObserver(_subagents())

        announced = await self._forward(
            observer,
            sink,
            FunctionToolCallEvent(
                part=ToolCallPart(tool_name="search_docs", args={}, tool_call_id="sub-1")
            ),
        )

        assert announced == [
            {
                "delegationId": "call-1",
                "agent": "researcher",
                "phase": "tool_call",
                "status": "researcher: calling search_docs",
                "tool": {"toolCallId": "sub-1", "name": "search_docs", "ok": None},
            }
        ]

    async def test_a_child_tool_result_reports_whether_it_landed(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        observer = SubAgentObserver(_subagents())

        announced = await self._forward(
            observer,
            sink,
            FunctionToolResultEvent(
                part=ToolReturnPart(tool_name="search_docs", content="ok", tool_call_id="sub-1")
            ),
            # A RetryPromptPart is the child's tool asking the child's model to try again.
            FunctionToolResultEvent(
                part=RetryPromptPart(
                    tool_name="search_docs", content="try again", tool_call_id="sub-2"
                )
            ),
        )

        assert [(v["tool"]["toolCallId"], v["tool"]["ok"]) for v in announced] == [
            ("sub-1", True),
            ("sub-2", False),
        ]

    async def test_a_child_tool_call_marked_retried_did_not_land(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        # From pydantic-ai 2.60 the retry is the call's own part with outcome retried.
        observer = SubAgentObserver(_subagents())

        announced = await self._forward(
            observer,
            sink,
            FunctionToolResultEvent(
                part=ToolReturnPart(
                    tool_name="search_docs",
                    content="try again",
                    tool_call_id="sub-1",
                    outcome="retried",
                )
            ),
        )

        assert [(v["tool"]["ok"], v["status"]) for v in announced] == [
            (False, "researcher: search_docs failed")
        ]

    async def test_the_childs_own_prose_is_not_forwarded(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        # Progress is a status line, not a second transcript.
        observer = SubAgentObserver(_subagents())

        announced = await self._forward(
            observer, sink, TextPartDelta(content_delta="thinking out loud")
        )

        assert announced == []

    async def test_a_handler_called_outside_a_delegation_announces_nothing(
        self, sink: asyncio.Queue[BaseEvent]
    ) -> None:
        # Unreachable on the path that installs it, but checked: a handler is a plain callable.
        observer = SubAgentObserver(_subagents())

        await observer._forward_child_events(
            None,
            _events(
                FunctionToolCallEvent(
                    part=ToolCallPart(tool_name="search_docs", args={}, tool_call_id="sub-1")
                )
            ),
        )

        assert _drain(sink) == []
