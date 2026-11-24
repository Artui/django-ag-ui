from __future__ import annotations

import re
import sys
from typing import Any, get_args

import pytest
from django.test import RequestFactory
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai_harness.detect_loops import DetectLoops, LoopPattern

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.utils import run_error_message
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_agent_session import _events, _RecordingAudit, _run_input

LOOP_TEXT = re.compile(r"run failed: The run was stopped in a loop \((\w+)\): .+")


def _looping_agent() -> Agent[Any, Any]:
    calls = iter(range(100))

    async def stream_fn(messages: list, info: Any) -> Any:
        yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id=f"c{next(calls)}")}

    agent: Agent[Any, Any] = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def lookup() -> str:
        return "same"

    return agent


async def test_a_run_detect_loops_stops_records_the_loop_it_stopped_in() -> None:
    audit = _RecordingAudit()
    session = AgentSession(
        _looping_agent(),
        _run_input(),
        RequestFactory().post("/agent/"),
        deps=AgentDeps(user=None),
        audit_logger=audit,
        config=build_ag_ui_config(),
        conversation_store=NullConversationStore(),
        capabilities=[DetectLoops(action="error")],
    )

    joined = await _events(session)

    (run_event,) = [e for e in audit.events if e.tool_name == "agent.run"]
    match = LOOP_TEXT.fullmatch(run_event.error or "")
    assert match is not None and match.group(1) in get_args(LoopPattern)
    assert "stopped in a loop" not in joined


def test_without_the_harness_the_message_is_left_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pydantic_ai_harness.detect_loops", None)

    assert run_error_message(RuntimeError("kaboom"), "kaboom") == "kaboom"


def test_another_error_keeps_its_message() -> None:
    assert run_error_message(RuntimeError("kaboom"), "kaboom") == "kaboom"


def _loop_error() -> Exception:
    from pydantic_ai_harness.detect_loops import DetectedLoop, LoopDetectedError

    loop = DetectedLoop(
        pattern="repeated_call",
        tool_names=("t",),
        count=3,
        window=3,
        fingerprints=(),
        message="again",
    )
    return LoopDetectedError(loop)


def test_a_loop_error_inside_a_task_group_still_names_the_loop() -> None:
    error = ExceptionGroup("tg", [ExceptionGroup("inner", [_loop_error()])])

    assert run_error_message(error, "unhandled errors in a TaskGroup").startswith(
        "The run was stopped in a loop (repeated_call): "
    )


def test_a_task_group_without_a_loop_error_keeps_its_message() -> None:
    error = ExceptionGroup("tg", [RuntimeError("kaboom")])

    assert run_error_message(error, "tg") == "tg"
