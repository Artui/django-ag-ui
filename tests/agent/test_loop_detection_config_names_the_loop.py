from __future__ import annotations

from typing import Any

from django.test import RequestFactory
from django_spec_agents import LoopDetectionConfig
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from pydantic_ai import FunctionToolset
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from django_ag_ui import AgentConfig, ToolRegistry, build_agent
from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_a_loop_stop_names_the_loop import LOOP_TEXT
from tests.agent.test_agent_session import _events, _RecordingAudit, _run_input


async def test_an_agent_configs_loop_detection_names_the_loop_it_stopped() -> None:
    calls = iter(range(100))

    async def stream_fn(messages: list, info: Any) -> Any:
        yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id=f"c{next(calls)}")}

    def lookup() -> str:
        return "same"

    toolset = FunctionToolset([lookup])
    agent = build_agent(
        ToolRegistry(),
        AgentConfig(
            model=FunctionModel(stream_function=stream_fn),
            toolsets=[toolset],
            loop_detection=LoopDetectionConfig(action="error"),
        ),
    )
    audit = _RecordingAudit()

    await _events(
        AgentSession(
            agent,
            _run_input(),
            RequestFactory().post("/agent/"),
            deps=AgentDeps(user=None),
            audit_logger=audit,
            config=build_ag_ui_config(),
            conversation_store=NullConversationStore(),
        )
    )

    (run_event,) = [e for e in audit.events if e.tool_name == "agent.run"]
    assert LOOP_TEXT.fullmatch(run_event.error or "") is not None
