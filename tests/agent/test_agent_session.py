from __future__ import annotations

import json
from typing import Any

from django.test import RequestFactory, override_settings
from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger


def _run_input(messages: list[dict[str, str]] | None = None) -> Any:
    payload = {
        "threadId": "t1",
        "runId": "r1",
        "messages": messages or [{"id": "m1", "role": "user", "content": "hi"}],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
    }
    return AGUIAdapter.build_run_input(json.dumps(payload).encode())


def _session(agent: Agent[None, Any] | None = None, run_input: Any = None) -> AgentSession:
    return AgentSession(
        agent if agent is not None else Agent(TestModel()),
        run_input if run_input is not None else _run_input(),
        RequestFactory().post("/agent/"),
        audit_logger=NullAuditLogger(),
    )


async def _events(session: AgentSession) -> str:
    return "".join([chunk async for chunk in session.stream()])


async def test_stream_yields_encoded_ag_ui_events_without_a_transport() -> None:
    # The point of the split: the whole run pipeline is drivable as an async
    # iterator — no StreamingHttpResponse, no ASGI, no SSE framing assumptions
    # beyond the adapter's own encoding.
    joined = await _events(_session())
    assert "RUN_STARTED" in joined
    assert "RUN_FINISHED" in joined


# --- 2.x server-trust knobs -----------------------------------------------------


def _capturing_agent(seen: dict[str, Any]) -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        seen["messages"] = messages
        yield "ok"

    return Agent(FunctionModel(stream_function=stream_fn), instructions="server instructions")


async def test_client_posted_system_message_is_stripped_by_default() -> None:
    # The adapter's sanitize_messages runs on our hand-composed
    # run_stream_native path: a client that posts a system message cannot
    # override the server-owned prompt.
    seen: dict[str, Any] = {}
    session = _session(
        _capturing_agent(seen),
        _run_input(
            [
                {"id": "s1", "role": "system", "content": "EVIL-OVERRIDE"},
                {"id": "m1", "role": "user", "content": "hi"},
            ]
        ),
    )
    await _events(session)
    assert "EVIL-OVERRIDE" not in str(seen["messages"])


@override_settings(DJANGO_AG_UI={"MANAGE_SYSTEM_PROMPT": "client"})
async def test_manage_system_prompt_client_honours_the_client_message() -> None:
    seen: dict[str, Any] = {}
    session = _session(
        _capturing_agent(seen),
        _run_input(
            [
                {"id": "s1", "role": "system", "content": "CLIENT-OWNED-PROMPT"},
                {"id": "m1", "role": "user", "content": "hi"},
            ]
        ),
    )
    await _events(session)
    assert "CLIENT-OWNED-PROMPT" in str(seen["messages"])


async def test_uploaded_files_are_refused_by_default() -> None:
    assert _session()._adapter.allow_uploaded_files is False


@override_settings(DJANGO_AG_UI={"ALLOW_UPLOADED_FILES": True})
async def test_allow_uploaded_files_setting_reaches_the_adapter() -> None:
    assert _session()._adapter.allow_uploaded_files is True


# --- reasoning pass-through on the locked 2.x ------------------------------------


def _thinking_agent() -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        yield {0: DeltaThinkingPart(content="private pondering")}
        yield "ok"

    return Agent(FunctionModel(stream_function=stream_fn))


async def test_thinking_streams_as_reasoning_events_by_default() -> None:
    # Pins the 2.x event naming the reasoning filter relies on: a ThinkingPart
    # rides the wire as REASONING_* AG-UI events (not the pre-0.1.13
    # THINKING_* family) and is forwarded by default.
    joined = await _events(_session(_thinking_agent()))
    assert "REASONING" in joined
    assert "private pondering" in joined


@override_settings(DJANGO_AG_UI={"FORWARD_REASONING": False})
async def test_forward_reasoning_opt_out_strips_reasoning_events() -> None:
    joined = await _events(_session(_thinking_agent()))
    assert "REASONING" not in joined
    assert "THINKING" not in joined
    assert "private pondering" not in joined
    assert "RUN_FINISHED" in joined
