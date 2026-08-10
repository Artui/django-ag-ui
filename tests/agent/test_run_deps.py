"""The run's ``deps`` — what the endpoint hands the agent for each run."""

from __future__ import annotations

import inspect
import json
from typing import Any

from django.http import StreamingHttpResponse
from django.test import RequestFactory
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import authenticated_user


def _body() -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "who am i"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()


def _post(user: Any = ...) -> Any:
    request = RequestFactory().post("/agent/", data=_body(), content_type="application/json")
    if user is not ...:
        request.user = user
    return request


async def _drain(response: StreamingHttpResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.streaming_content:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


def _recording_registry(seen: list[Any]) -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg)
    def whoami(ctx: RunContext[AgentDeps]) -> str:
        """Report the acting user."""
        seen.append(ctx.deps.user)
        return "ok"

    return reg


class TestTheSessionPassesDeps:
    async def test_a_tool_reads_the_acting_user_off_ctx_deps(self) -> None:
        """The whole seam in one assertion: request-scoped values reach a tool
        through ``ctx.deps`` rather than a closure over the request."""
        seen: list[Any] = []
        agent: Agent[AgentDeps, Any] = Agent(TestModel(), deps_type=AgentDeps)

        @agent.tool
        def whoami(ctx: RunContext[AgentDeps]) -> str:
            """Whoami."""
            seen.append(ctx.deps.user)
            return "ok"

        user = object()
        session = AgentSession(
            agent,
            AGUIAdapter.build_run_input(_body()),
            RequestFactory().post("/agent/"),
            deps=AgentDeps(user=user),
            audit_logger=NullAuditLogger(),
            config=build_ag_ui_config(),
            conversation_store=NullConversationStore(),
        )
        async for _ in session.stream():
            pass

        assert seen == [user]

    def test_deps_are_required_not_defaulted(self) -> None:
        """A forgotten ``deps`` would mean spec tools silently acting as nobody,
        so the transport has to say who is acting."""
        param = inspect.signature(AgentSession.__init__).parameters["deps"]
        assert param.default is inspect.Parameter.empty


class TestTheViewBuildsThem:
    async def test_the_acting_user_reaches_a_tool_end_to_end(self) -> None:
        seen: list[Any] = []
        # Authenticated, because the endpoint refuses anonymous callers before
        # any of this runs — the identity assertion is unchanged.
        user = authenticated_user()
        view = DjangoAGUIView(_recording_registry(seen), model=TestModel())

        await _drain(await view(_post(user)))

        assert seen == [user]

    async def test_a_request_with_no_user_is_an_anonymous_run(self) -> None:
        """An endpoint served without Django's auth middleware has no ``user``
        attribute at all. The package's own ``materialize_request_user`` treats
        that as ``None``; the run must not raise ``AttributeError`` instead.

        Such a deployment is exactly the one that has to waive the
        authentication requirement — there is no middleware to satisfy it."""
        seen: list[Any] = []
        view = DjangoAGUIView(
            _recording_registry(seen), model=TestModel(), require_authenticated=False
        )

        await _drain(await view(_post()))

        assert seen == [None]
