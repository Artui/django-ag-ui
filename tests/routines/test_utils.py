from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.db import transaction
from django.http import StreamingHttpResponse
from django.test import RequestFactory
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)
from django_spec_agents import DESTRUCTIVE_METADATA_KEY, ToolGuard, ToolGuardConfig
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_mcp import MCPServer
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from django_ag_ui import AGUIServer
from django_ag_ui.agent.resource_invalidation import INVALIDATE_EVENT_NAME
from django_ag_ui.routines.utils import announce_routine_saved, grantable_tool_defs
from tests.routineapp.models import TeamRoutine
from tests.routineapp.people import person, routine


def _send(data: Any = None) -> Any:
    """Send an email."""
    return data


def _read(data: Any = None) -> Any:
    """Read the inbox."""
    return data


def _specs() -> dict[str, Any]:
    reads = Parameters.of(Parameter("to", "string", required=True))
    return {
        "send_email": ServiceSpec(service=_send, reads=reads, permissions=[Unrestricted()]),
        "read_inbox": SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_read, permissions=[Unrestricted()]
        ),
        "create_routine": SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_read, permissions=[Unrestricted()]
        ),
    }


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    @tool(registry, name="send_email")
    def send_email(to: str) -> str:
        return to

    return registry


def _guard() -> ToolGuard:
    return ToolGuard(ToolRegistry(), config=ToolGuardConfig(exempt=frozenset()))


def _request() -> Any:
    request = RequestFactory().get("/")
    request.user = None
    return request


def test_specs_are_grantable_a_service_as_destructive_and_a_selector_not() -> None:
    defs = grantable_tool_defs(ToolRegistry(), _request(), service_specs=_specs())
    assert [(d.name, _guard().requires_approval(d)) for d in defs] == [
        ("send_email", True),
        ("read_inbox", False),
    ]


def test_the_registry_wins_a_name_another_source_also_offers() -> None:
    defs = grantable_tool_defs(_registry(), _request(), service_specs=_specs())
    assert [d.name for d in defs] == ["send_email", "read_inbox"]
    assert _guard().requires_approval(defs[0]) is False


@pytest.mark.django_db
def test_a_bridged_mcp_servers_tools_are_grantable_as_the_bridge_marks_them() -> None:
    server = MCPServer(name="mail")
    reads = Parameters.of(Parameter("to", "string", required=True))
    server.register_tool(
        ServiceSpec(service=_send, reads=reads, permissions=[Unrestricted()], atomic=False),
        name="send_email",
        description="Send.",
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_read, permissions=[Unrestricted()]),
        name="read_inbox",
        description="Read.",
    )
    request = _request()
    request.user = person("ann")
    defs = grantable_tool_defs(ToolRegistry(), request, mcp_server=server)
    by_name = {d.name: d for d in defs}
    assert (by_name["send_email"].metadata or {}).get(DESTRUCTIVE_METADATA_KEY) is True
    assert by_name["read_inbox"].metadata is None


class _PagedServer:
    """Two pages of ``tools/list``, the shape django-spec-mcp's ``list_tools`` returns."""

    def list_tools(self, cursor: str | None, **kwargs: Any) -> dict[str, Any]:
        if cursor is None:
            tool_a = {"name": "a", "inputSchema": {}, "annotations": {"readOnlyHint": False}}
            return {"tools": [tool_a], "nextCursor": "2"}
        tool_b = {"name": "b", "inputSchema": {}, "annotations": {"destructiveHint": False}}
        return {"tools": [tool_b]}


class _BrokenServer:
    def list_tools(self, cursor: str | None, **kwargs: Any) -> JsonRpcError:
        return JsonRpcError(code=-32603, message="boom")


def test_every_page_of_the_mcp_listing_is_read() -> None:
    defs = grantable_tool_defs(ToolRegistry(), _request(), mcp_server=_PagedServer())
    assert [(d.name, d.metadata) for d in defs] == [
        ("a", {DESTRUCTIVE_METADATA_KEY: True}),
        ("b", None),
    ]


def test_a_failed_mcp_listing_raises() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        grantable_tool_defs(ToolRegistry(), _request(), mcp_server=_BrokenServer())


@pytest.mark.django_db
def test_a_save_off_any_run_announces_nothing() -> None:
    ann = person("ann")
    announce_routine_saved(TeamRoutine, routine(ann))


def _saving_model() -> FunctionModel:
    turns: list[Any] = [{0: DeltaToolCall(name="save", json_args="{}", tool_call_id="c1")}]

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        yield turns.pop(0) if turns else "Done."

    return FunctionModel(stream_function=stream)


async def _invalidations(registry: ToolRegistry, ann: Any) -> list[Any]:
    server = AGUIServer(registry, model=_saving_model(), routine_model=TeamRoutine, id="helper")
    patterns, _, _ = server.urls
    endpoint = next(p.callback for p in patterns if p.name == "endpoint")
    body = {
        "threadId": "t1",
        "runId": "r1",
        "state": {},
        "messages": [{"id": "u1", "role": "user", "content": "Go."}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    request = RequestFactory().post("/", data=json.dumps(body), content_type="application/json")
    request.user = ann
    response = await endpoint(request)
    assert isinstance(response, StreamingHttpResponse)
    text = ""
    async for chunk in response.streaming_content:
        text += chunk if isinstance(chunk, str) else chunk.decode()
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    return [e["value"] for e in events if e.get("name") == INVALIDATE_EVENT_NAME]


@pytest.mark.django_db(transaction=True)
async def test_a_run_that_saves_a_routine_invalidates_the_routines_keys() -> None:
    ann = await sync_to_async(person)("ann")
    saved: list[int] = []
    registry = ToolRegistry()

    @tool(registry, name="save")
    def save() -> str:
        saved.append(routine(ann).pk)
        return "saved"

    assert await _invalidations(registry, ann) == [
        {"keys": ["routines", f"routines/{saved[0]}"], "reason": "routine_saved"}
    ]


@pytest.mark.django_db(transaction=True)
async def test_a_routine_save_that_rolls_back_invalidates_nothing() -> None:
    ann = await sync_to_async(person)("ann")
    registry = ToolRegistry()

    @tool(registry, name="save")
    def save() -> str:
        with contextlib.suppress(RuntimeError), transaction.atomic():
            routine(ann)
            raise RuntimeError("roll back")
        return "rolled back"

    assert await _invalidations(registry, ann) == []
    assert not await TeamRoutine.objects.aexists()
