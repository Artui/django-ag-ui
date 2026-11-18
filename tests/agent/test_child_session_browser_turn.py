from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import StreamingHttpResponse
from django_spec_agents.child_sessions.types.inbound_kind import InboundKind
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.types.child_lineage_record import ChildLineageRecord
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from django_ag_ui import AGUIServer, ToolRegistry, tool
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory

_PARENT = ThreadRef("t-parent", "planner")
_LINEAGE = ChildLineageRecord(root=_PARENT, depth=1, tools=["lookup", "write"], gated=["write"])


def _registry(calls: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    @tool(registry)
    def lookup(key: str) -> str:
        """Look a key up."""
        calls.append(f"lookup {key}")
        return key

    @tool(registry)
    def write(key: str) -> str:
        """Write a key."""
        calls.append(f"write {key}")
        return key

    @tool(registry)
    def wider(key: str) -> str:
        """A tool the parent never had."""
        calls.append(f"wider {key}")
        return key

    return registry


def _model(offered: list[list[str]], *, call: str | None) -> FunctionModel:
    async def stream_fn(messages: list, info: AgentInfo) -> Any:
        offered.append(sorted(t.name for t in info.function_tools))
        if call is not None and len(offered) == 1:
            yield {0: DeltaToolCall(name=call, json_args='{"key": "k"}', tool_call_id="c1")}
        else:
            yield "done"

    return FunctionModel(stream_function=stream_fn)


def _server(calls: list[str], offered: list[list[str]], **overrides: Any) -> AGUIServer:
    options: dict[str, Any] = {
        "model": _model(offered, call=overrides.pop("call", None)),
        "csrf_exempt": True,
        "conversation_store": DefaultConversationStore(),
        "run_store": DefaultRunStore(),
        "config": build_ag_ui_config(heartbeat_seconds=0),
        "id": "helper",
        "inbound_queue": DefaultInboundQueue(),
    }
    options.update(overrides)
    return AGUIServer(_registry(calls), **options)


def _body(thread_id: str) -> bytes:
    return json.dumps(
        {
            "threadId": thread_id,
            "runId": "r-browser",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "go on"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


async def _turn(server: AGUIServer, thread_id: str) -> str:
    patterns, _, _ = server.urls
    view = next(p for p in patterns if p.name == "endpoint").callback
    request = AuthedRequestFactory().post(
        "/agent/", data=_body(thread_id), content_type="application/json"
    )
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    chunks = [c if isinstance(c, str) else c.decode() async for c in response.streaming_content]
    return "".join(chunks)


async def _seed_child(thread_id: str, *, lineage: ChildLineageRecord | None = _LINEAGE) -> Any:
    request = AuthedRequestFactory().get("/")
    child = Conversation(thread_id, messages=[], owner_id="1", parent=_PARENT, lineage=lineage)
    await DefaultConversationStore().save(child, request=request)
    return request


@pytest.mark.django_db(transaction=True)
async def test_a_browser_turn_into_a_child_sees_only_its_parents_tools() -> None:
    calls: list[str] = []
    offered: list[list[str]] = []
    await _seed_child("t-child")

    await _turn(_server(calls, offered), "t-child")

    assert offered[0] == ["lookup", "write"]


@pytest.mark.django_db(transaction=True)
async def test_a_tool_the_parent_gated_still_asks_for_approval_in_a_browser_turn() -> None:
    calls: list[str] = []
    queue = DefaultInboundQueue()
    request = await _seed_child("t-child")

    body = await _turn(_server(calls, [], call="write", inbound_queue=queue), "t-child")

    assert '"type":"interrupt"' in body
    assert calls == []
    assert await queue.pending(_PARENT, request=request) == []


@pytest.mark.django_db(transaction=True)
async def test_a_browser_turn_into_a_child_reports_to_its_parent_when_it_settles() -> None:
    queue = DefaultInboundQueue()
    request = await _seed_child("t-child")

    await _turn(_server([], [], inbound_queue=queue), "t-child")

    reports = await queue.pending(_PARENT, request=request)
    assert [(m.kind, m.sender) for m in reports] == [
        (InboundKind.REPORT, ThreadRef("t-child", "helper"))
    ]


@pytest.mark.django_db(transaction=True)
async def test_a_thread_with_no_lineage_keeps_every_tool() -> None:
    offered: list[list[str]] = []
    await _seed_child("t-plain", lineage=None)

    await _turn(_server([], offered), "t-plain")

    assert offered[0] == ["lookup", "wider", "write"]


@pytest.mark.django_db(transaction=True)
async def test_a_lineage_with_no_parent_runs_on_a_mount_with_no_queue() -> None:
    offered: list[list[str]] = []
    request = AuthedRequestFactory().get("/")
    orphan = Conversation("t-orphan", messages=[], owner_id="1", lineage=_LINEAGE)
    await DefaultConversationStore().save(orphan, request=request)

    await _turn(_server([], offered, inbound_queue=None), "t-orphan")

    assert offered[0] == ["lookup", "wider", "write"]


@pytest.mark.django_db(transaction=True)
async def test_a_child_turn_on_a_mount_with_no_queue_is_refused() -> None:
    await _seed_child("t-child")

    with pytest.raises(ImproperlyConfigured, match="needs its mount's id= and inbound_queue="):
        await _turn(_server([], [], inbound_queue=None), "t-child")


def test_an_inbound_queue_needs_the_mounts_id() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"inbound_queue=\) needs id="):
        _server([], [], id=None)
