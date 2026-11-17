from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from django.test import RequestFactory, override_settings
from django_spec_agents.agent.types.agent_deps import AgentDeps
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config


class _Store:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    async def save(self, conversation: Any, *, request: Any) -> None:
        self.saved.append(conversation)

    async def load(self, thread_id: str, *, request: Any) -> Any:
        return None

    async def list_threads(self, *, request: Any) -> list[Any]:
        return []

    async def delete(self, thread_id: str, *, request: Any) -> bool:
        return False

    async def rename(self, thread_id: str, title: str, *, request: Any) -> bool:
        return False


class _Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any) -> None:
        self.events.append(event)


def _parked_agent() -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        yield "partial answer"
        await asyncio.Event().wait()

    return Agent(FunctionModel(stream_function=stream_fn))


def _session(*, protocol_version: str | None, store: _Store, audit: _Audit, timeout: float | None):
    payload: dict[str, Any] = {
        "threadId": "t1",
        "runId": "r1",
        "messages": [{"id": "m1", "role": "user", "content": "hi"}],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
    }
    if protocol_version is not None:
        payload["protocolVersion"] = protocol_version
    return AgentSession(
        _parked_agent(),
        AGUIAdapter.build_run_input(json.dumps(payload).encode()),
        RequestFactory().post("/agent/"),
        deps=AgentDeps(user=None),
        audit_logger=audit,
        config=build_ag_ui_config(run_timeout_seconds=timeout),
        conversation_store=store,
    )


async def _wire(session: AgentSession) -> list[dict[str, Any]]:
    async def drain() -> list[str]:
        return [chunk async for chunk in session.stream()]

    chunks = await asyncio.wait_for(drain(), timeout=5)
    return [
        json.loads(line.removeprefix("data: "))
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]


async def test_a_timed_out_run_finishes_cancelled_for_a_client_declaring_1_0() -> None:
    store, audit = _Store(), _Audit()

    events = await _wire(_session(protocol_version="1.0", store=store, audit=audit, timeout=0.05))

    assert [e["type"] for e in events][-1] == "RUN_FINISHED"
    assert events[-1]["outcome"] == {"type": "cancelled"}
    assert "RUN_ERROR" not in [e["type"] for e in events]


async def test_a_timed_out_run_finishes_bare_for_a_client_that_declares_nothing() -> None:
    store, audit = _Store(), _Audit()

    events = await _wire(_session(protocol_version=None, store=store, audit=audit, timeout=0.05))

    assert events[-1]["type"] == "RUN_FINISHED"
    assert "outcome" not in events[-1]


async def test_a_timed_out_run_keeps_the_partial_transcript_and_audits_it() -> None:
    store, audit = _Store(), _Audit()

    await _wire(_session(protocol_version="1.0", store=store, audit=audit, timeout=0.05))

    (conversation,) = store.saved
    assert [m["role"] for m in conversation.messages] == ["user", "assistant"]
    assert "partial answer" in conversation.messages[1]["content"]
    (event,) = [e for e in audit.events if e.tool_name == "agent.run"]
    assert event.success is False
    assert "timed out" in (event.error or "")


async def test_no_timeout_leaves_a_run_unbounded() -> None:
    store, audit = _Store(), _Audit()
    session = _session(protocol_version="1.0", store=store, audit=audit, timeout=None)
    stream = session.stream()

    async for chunk in stream:
        if "partial answer" in chunk:
            break
    await asyncio.sleep(0.1)
    await stream.aclose()

    assert [e for e in audit.events if "timed out" in (e.error or "")] == []


def test_the_run_timeout_is_off_by_default_and_configurable() -> None:
    with override_settings(DJANGO_AG_UI={}):
        assert build_ag_ui_config().run_timeout_seconds is None
    with override_settings(DJANGO_AG_UI={"RUN_TIMEOUT_SECONDS": 90}):
        assert build_ag_ui_config().run_timeout_seconds == 90.0
    assert build_ag_ui_config(run_timeout_seconds=5).run_timeout_seconds == 5.0
    with override_settings(DJANGO_AG_UI={"RUN_TIMEOUT_SECONDS": 0}):
        assert build_ag_ui_config().run_timeout_seconds is None


def test_a_negative_timeout_is_refused() -> None:
    with pytest.raises(Exception, match="RUN_TIMEOUT_SECONDS"):
        build_ag_ui_config(run_timeout_seconds=-1)
