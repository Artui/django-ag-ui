"""``deps_factory`` — supplying the per-run dependencies yourself."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, StreamingHttpResponse
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.authed_request_factory import AuthedRequestFactory, authenticated_user


class _Doc(BaseModel):
    document: str = ""


@dataclass
class _TenantDeps(AgentDeps):
    """A project's own deps — the shape `deps_factory` exists to allow."""

    tenant: str = ""


def _body(state: Any = None) -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": state,
            "messages": [{"id": "u1", "role": "user", "content": "go"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


def _post(state: Any = None) -> Any:
    # Authenticated: the endpoint refuses anonymous callers by default, and
    # nothing here is about that refusal.
    return AuthedRequestFactory().post(
        "/agent/", data=_body(state), content_type="application/json"
    )


async def _drain(response: StreamingHttpResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.streaming_content:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


def _recording_registry(seen: list[Any]) -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg)
    def inspect_deps(ctx: RunContext[AgentDeps]) -> str:
        """Record the run's deps."""
        seen.append(ctx.deps)
        return "ok"

    return reg


class TestTheDefault:
    async def test_binds_the_acting_user(self) -> None:
        seen: list[Any] = []
        view = DjangoAGUIView(_recording_registry(seen), model=TestModel())
        request = _post()
        # An opaque stand-in that also clears the authentication gate, so the
        # identity assertion below is about deps binding and nothing else.
        user = authenticated_user()
        request.user = user

        await _drain(await view(request))

        assert seen[0].user is user


class TestACustomFactory:
    async def test_takes_over_entirely(self) -> None:
        seen: list[Any] = []
        view = DjangoAGUIView(
            _recording_registry(seen),
            model=TestModel(),
            deps_factory=lambda _request: _TenantDeps(user="alice", tenant="acme"),
        )

        await _drain(await view(_post()))

        assert (seen[0].tenant, seen[0].user) == ("acme", "alice")

    async def test_receives_the_request(self) -> None:
        """So a project can read a header, a subdomain, or session data."""
        received: list[HttpRequest] = []
        view = DjangoAGUIView(
            ToolRegistry(),
            model=TestModel(),
            deps_factory=lambda request: received.append(request) or AgentDeps(user=None),  # type: ignore[func-returns-value]
        )

        await _drain(await view(_post()))

        assert received[0].path == "/agent/"

    async def test_seeding_state_with_a_model_gets_it_validated(self) -> None:
        """The gap this exists to close: pydantic-ai validates inbound state
        against ``type(deps.state)``, so validation is only possible when the
        deps arrive pre-seeded with a model instance."""
        seen: list[Any] = []
        view = DjangoAGUIView(
            _recording_registry(seen),
            model=TestModel(),
            deps_factory=lambda _request: AgentDeps(user=None, state=_Doc()),
        )

        await _drain(await view(_post(state={"document": "from the client"})))

        assert isinstance(seen[0].state, _Doc)
        assert seen[0].state.document == "from the client"

    async def test_without_a_factory_state_stays_an_unvalidated_mapping(self) -> None:
        """The contrast: the default deps leave ``state`` a plain dict."""
        seen: list[Any] = []
        view = DjangoAGUIView(_recording_registry(seen), model=TestModel())

        await _drain(await view(_post(state={"document": "from the client"})))

        assert seen[0].state == {"document": "from the client"}
        assert not isinstance(seen[0].state, BaseModel)


class TestTheServerThreadsIt:
    async def test_agui_server_forwards_it_to_the_view(self) -> None:
        seen: list[Any] = []
        server = AGUIServer(
            _recording_registry(seen),
            model=TestModel(),
            # ``user`` is required from django-pydantic-agent 0.18; the sibling
            # cases above already name it, this one had been leaning on the default.
            deps_factory=lambda _request: _TenantDeps(user="alice", tenant="acme"),
        )

        await _drain(await server._view(_post()))

        assert seen[0].tenant == "acme"
