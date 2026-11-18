from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import AsyncRequestFactory, override_settings
from django.urls import reverse
from django_service_specs import Parameter, Parameters, ServiceSpec, Unrestricted
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_mcp import MCPServer, UIToolMeta, UIVisibility
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.abstract import AbstractToolset
from pydantic_ai.toolsets.function import FunctionToolset

from django_ag_ui.agent.agui_server import AGUIServer
from tests.authed_request_factory import AuthedAsyncRequestFactory

_VIEW_URI = "ui://counter/view"
_VIEW_HTML = "<!doctype html><p>counter</p>"
_ONE_NUMBER = Parameters.of(Parameter("n", "integer", required=True))


def _bump(*, n: int) -> dict[str, Any]:
    return {"count": n + 1}


def _apps_server() -> MCPServer:
    server = MCPServer(name="apps")
    server.register_ui_resource(name="counter", uri=_VIEW_URI, html=_VIEW_HTML)
    spec = ServiceSpec(service=_bump, reads=_ONE_NUMBER, permissions=[Unrestricted()])
    for name, visibility in (("bump", UIVisibility.APP), ("model_bump", UIVisibility.MODEL)):
        server.register_tool(
            spec, name=name, description="Bump.", ui=UIToolMeta(_VIEW_URI, (visibility,))
        )
    return server


class MCPError(RuntimeError):
    """``pydantic_ai.mcp.MCPError``'s shape; that module needs fastmcp, absent here."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.message, self.code = message, code


class _RemoteToolset(AbstractToolset[Any]):
    """Stands in for pydantic-ai's ``MCPToolset``: same id, method names and ``MCPError``."""

    def __init__(self, id: str | None, *, fail: BaseException | None = None) -> None:
        self._id, self._fail, self.calls = id, fail, []

    @property
    def id(self) -> str | None:
        return self._id

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        return {}

    async def call_tool(self, name: str, tool_args: Any, ctx: Any, tool: Any) -> Any: ...

    async def call_tool_for_app(self, name: str, arguments: Any = None) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if self._fail is not None:
            raise self._fail
        return {"content": [], "structuredContent": {"name": name}, "isError": False}

    async def read_resource_for_app(self, uri: str) -> dict[str, Any]:
        return {"contents": [{"uri": uri, "text": "remote"}]}


class _Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any) -> None:
        self.events.append(event)


def _route(**kwargs: Any) -> Any:
    kwargs.setdefault("csrf_exempt", True)
    patterns, _, _ = AGUIServer(ToolRegistry(), model=TestModel(), **kwargs).urls
    return {pattern.name: pattern.callback for pattern in patterns}["mcp_apps"]


def _post(body: Any, *, factory: Any = None) -> Any:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    factory = factory or AuthedAsyncRequestFactory()
    return factory.post("/agent/mcp-apps/", data=raw, content_type="application/json")


async def _answer(route: Any, body: Any) -> tuple[int, dict[str, Any]]:
    response = await route(_post(body))
    return response.status_code, json.loads(response.content)


def _call(server_id: str, name: str = "bump", arguments: Any = None) -> dict[str, Any]:
    params = {"name": name, "arguments": {"n": 1} if arguments is None else arguments}
    return {"serverId": server_id, "method": "tools/call", "params": params}


def _read(server_id: str, uri: str = _VIEW_URI) -> dict[str, Any]:
    return {"serverId": server_id, "method": "resources/read", "params": {"uri": uri}}


@pytest.mark.django_db
async def test_a_tools_call_reaches_the_in_process_bridge_as_the_user() -> None:
    status, body = await _answer(_route(mcp_server=_apps_server()), _call("mcp"))
    assert status == 200
    assert body["result"]["structuredContent"] == {"count": 2}


@pytest.mark.django_db
async def test_a_resources_read_returns_the_views_html() -> None:
    status, body = await _answer(_route(mcp_server=_apps_server()), _read("mcp"))
    assert status == 200
    assert body["result"]["contents"][0]["text"] == _VIEW_HTML


@pytest.mark.django_db
async def test_a_model_only_tool_is_refused_to_the_app() -> None:
    status, body = await _answer(_route(mcp_server=_apps_server()), _call("mcp", "model_bump"))
    assert status == 200
    assert body["error"]["code"] == -32602
    assert "not available to apps" in body["error"]["message"]


async def test_a_wrapped_toolset_is_found_by_its_id() -> None:
    remote = _RemoteToolset("remote")
    wrapped = remote.prefixed("r").filtered(lambda ctx, tool: True)
    route = _route(toolsets=[wrapped])
    assert (await _answer(route, _call("remote")))[1]["result"]["structuredContent"] == {
        "name": "bump"
    }
    assert (await _answer(route, _read("remote")))[1]["result"]["contents"][0]["uri"] == _VIEW_URI
    assert remote.calls == [("bump", {"n": 1})]


async def test_a_raised_mcp_error_keeps_its_code() -> None:
    route = _route(toolsets=[_RemoteToolset("remote", fail=MCPError("no such tool", -32602))])
    assert (await _answer(route, _call("remote")))[1]["error"] == {
        "code": -32602,
        "message": "no such tool",
    }


class _HalfCoded(Exception):
    def __init__(self, **attributes: Any) -> None:
        super().__init__("half")
        self.__dict__.update(attributes)


@pytest.mark.parametrize("attributes", [{"code": 5}, {"code": "5", "message": "m"}])
async def test_a_failure_with_half_a_code_is_an_internal_error(attributes: Any) -> None:
    route = _route(toolsets=[_RemoteToolset("remote", fail=_HalfCoded(**attributes))])
    assert (await _answer(route, _call("remote")))[1]["error"]["code"] == -32603


async def test_an_uncoded_failure_is_an_internal_error_with_its_detail_audited() -> None:
    audit = _Audit()
    route = _route(toolsets=[_RemoteToolset("remote", fail=OSError("down"))], audit_logger=audit)
    _, body = await _answer(route, _call("remote"))
    assert body["error"] == {"code": -32603, "message": "The server failed the call"}
    assert "OSError('down')" in audit.events[-1].error


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"{not json", -32700),
        ([1], -32602),
        ({"serverId": "remote", "method": "tools/list"}, -32601),
        ({"serverId": "remote"}, -32601),
        ({"serverId": "remote", "method": ["tools/call"]}, -32601),
        ({"serverId": 3, "method": "tools/call"}, -32602),
        ({"serverId": "remote", "method": "tools/call", "params": []}, -32602),
        ({"serverId": "remote", "method": "tools/call", "params": {}}, -32602),
        ({"serverId": "remote", "method": "resources/read", "params": {}}, -32602),
        (_call("elsewhere"), -32602),
        (_call("plain"), -32601),
    ],
)
async def test_a_request_the_route_cannot_relay_gets_a_coded_error(body: Any, code: int) -> None:
    route = _route(toolsets=[_RemoteToolset("remote"), FunctionToolset(id="plain")])
    status, answer = await _answer(route, body)
    assert status == 200
    assert answer["error"]["code"] == code


async def test_a_null_server_id_never_reaches_a_toolset_without_one() -> None:
    nameless = _RemoteToolset(None)
    _, body = await _answer(_route(toolsets=[nameless]), {**_call("x"), "serverId": None})
    assert body["error"]["code"] == -32602
    assert nameless.calls == []


async def test_two_toolsets_with_one_id_are_refused_rather_than_guessed() -> None:
    route = _route(toolsets=[_RemoteToolset("twin"), _RemoteToolset("twin")])
    assert "more than one" in (await _answer(route, _call("twin")))[1]["error"]["message"]


async def test_an_anonymous_caller_is_refused_with_a_code() -> None:
    route = _route(toolsets=[_RemoteToolset("remote")])
    request = _post(_call("remote"), factory=AsyncRequestFactory())
    request.user = AnonymousUser()
    response = await route(request)
    assert response.status_code == 401
    assert json.loads(response.content)["error"]["code"] == -32001


async def test_a_caller_the_predicate_refuses_is_forbidden_with_a_code() -> None:
    remote = _RemoteToolset("remote")
    route = _route(toolsets=[remote], authorize=lambda request: False)
    response = await route(_post(_call("remote")))
    assert response.status_code == 403
    assert json.loads(response.content)["error"]["code"] == -32006
    assert remote.calls == []


async def test_a_get_is_not_allowed() -> None:
    route = _route(toolsets=[])
    assert (await route(AuthedAsyncRequestFactory().get("/agent/mcp-apps/"))).status_code == 405


async def test_each_call_is_audit_logged() -> None:
    audit = _Audit()
    route = _route(toolsets=[_RemoteToolset("remote")], audit_logger=audit)
    await _answer(route, _call("remote"))
    await _answer(route, _read("elsewhere"))
    await _answer(route, b"[")
    assert [(e.tool_name, e.success) for e in audit.events] == [
        ("mcp_apps.tools/call", True),
        ("mcp_apps.resources/read", False),
        ("mcp_apps", False),
    ]
    assert json.loads(audit.events[0].arguments_repr)["serverId"] == "remote"
    assert audit.events[1].error.startswith("-32602: ")


def test_the_route_shares_the_tools_routes_policy() -> None:
    patterns, _, _ = AGUIServer(ToolRegistry(), model=TestModel(), csrf_exempt=False).urls
    views = {pattern.name: pattern.callback for pattern in patterns}
    for attribute in ("csrf_exempt", "_require_authenticated", "_get_user"):
        assert getattr(views["mcp_apps"], attribute) == getattr(views["tools"], attribute)


@override_settings(ROOT_URLCONF="tests.agent.agui_server_urls")
def test_the_route_mounts_beside_tools() -> None:
    assert reverse("ag_ui:mcp_apps") == "/agent/mcp-apps/"


@pytest.mark.django_db
async def test_a_registry_tool_shadows_a_bridged_tool_for_apps_as_for_runs() -> None:
    registry = ToolRegistry()

    @tool(registry)
    def bump(n: int) -> int:
        """Shadows the bridged ``bump``."""
        return n

    patterns, _, _ = AGUIServer(
        registry, model=TestModel(), mcp_server=_apps_server(), csrf_exempt=True
    ).urls
    route = {pattern.name: pattern.callback for pattern in patterns}["mcp_apps"]
    assert (await _answer(route, _call("mcp")))[1]["error"]["code"] == -32602
