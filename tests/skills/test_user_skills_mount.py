from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.http import StreamingHttpResponse
from django.test import RequestFactory
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from django_ag_ui import SKILLS_CHANGED_EVENT_NAME, AGUIServer, SkillRegistry
from tests.skillapp.models import Tenant, TenantSkill, TenantSkillPolicy
from tests.skillapp.people import member, request_as, skill

_IDLE = FunctionModel(lambda messages, info: None)


def _registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.add("summarize", "Summarize")
    return registry


def _routes(server: AGUIServer) -> dict[str, Any]:
    patterns, _, _ = server.urls
    return {pattern.name: pattern.callback for pattern in patterns}


def _post(user: Any) -> Any:
    body = {
        "threadId": "t1",
        "runId": "r1",
        "state": {},
        "messages": [{"id": "u1", "role": "user", "content": "Go."}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    request = RequestFactory().post(
        "/agent/", data=json.dumps(body), content_type="application/json"
    )
    request.user = user
    return request


async def _events(server: AGUIServer, user: Any) -> list[dict[str, Any]]:
    response = await _routes(server)["endpoint"](_post(user))
    assert isinstance(response, StreamingHttpResponse)
    body = ""
    async for chunk in response.streaming_content:
        body += chunk if isinstance(chunk, str) else chunk.decode()
    lines = body.splitlines()
    return [json.loads(line[len("data: ") :]) for line in lines if line.startswith("data: ")]


def _calling(name: str, args: dict[str, Any], seen: list[str]) -> FunctionModel:
    """A model that records what it is told, calls ``name`` once, then answers."""
    turns: list[Any] = [
        {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id="c1")}
    ]

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        seen.append(f"{info.instructions}{messages}")
        yield turns.pop(0) if turns else "Done."

    return FunctionModel(stream_function=stream)


def _server(model: Any, **kwargs: Any) -> AGUIServer:
    return AGUIServer(
        ToolRegistry(), model=model, skills=_registry(), skill_policy=TenantSkillPolicy(), **kwargs
    )


def test_a_policy_alone_mounts_the_catalog_and_the_editor_needs_its_keyword() -> None:
    routes = _routes(AGUIServer(ToolRegistry(), model=_IDLE, skill_policy=TenantSkillPolicy()))
    assert "skills" in routes
    assert "skill_editor" not in routes
    server = AGUIServer(
        ToolRegistry(), model=_IDLE, skill_policy=TenantSkillPolicy(), skill_editor=True
    )
    editing = _routes(server)
    assert {"skill_editor", "skill_editor_item"} <= set(editing)


@pytest.mark.parametrize("keyword", ["skill_editor", "skill_authoring"])
def test_the_editor_and_the_tools_need_a_policy(keyword: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="need skill_policy="):
        AGUIServer(ToolRegistry(), model=_IDLE, **{keyword: True})


def test_a_dotted_path_policy_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="skill_policy"):
        AGUIServer(ToolRegistry(), model=_IDLE, skill_policy="app.policies.Skills")


@pytest.mark.django_db
def test_the_editor_refuses_a_name_the_registry_holds() -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    server = _server(_IDLE, skill_editor=True)
    body = {"name": "summarize", "description": "Mine.", "body": "Shorter."}
    response = _routes(server)["skill_editor"](request_as(ann, "post", body))
    assert response.status_code == 400
    assert not TenantSkill.objects.exists()


@pytest.mark.django_db(transaction=True)
async def test_each_member_runs_with_their_own_stored_skills() -> None:
    acme = await sync_to_async(Tenant.objects.create)(name="acme")
    ann, bob = await sync_to_async(member)("ann", acme), await sync_to_async(member)("bob", acme)
    await sync_to_async(skill)(acme, "ann-notes", ann)
    await sync_to_async(skill)(acme, "bob-notes", bob)
    seen: list[str] = []
    server = _server(_calling("load_capability", {"id": "ann-notes"}, seen))
    await _events(server, ann)
    assert "- ann-notes:" in seen[0] and "bob-notes" not in seen[0]
    assert "The secret steps of ann-notes." in seen[1]
    seen.clear()
    await _events(server, bob)
    assert "- bob-notes:" in seen[0] and "ann-notes" not in seen[0]


@pytest.mark.django_db(transaction=True)
async def test_a_run_that_saves_a_skill_announces_it() -> None:
    ann = await sync_to_async(member)("ann", await sync_to_async(Tenant.objects.create)(name="a"))
    args = {"name": "triage", "description": "Sort the inbox.", "body": "Read, then sort."}
    server = _server(_calling("create_skill", args, []), skill_authoring=True)
    events = await _events(server, ann)
    announced = [e for e in events if e.get("name") == SKILLS_CHANGED_EVENT_NAME]
    assert [e["value"] for e in announced] == [{"name": "triage"}]
    assert await sync_to_async(TenantSkill.objects.filter(name="triage").exists)()


@pytest.mark.django_db(transaction=True)
async def test_a_refused_write_announces_nothing() -> None:
    ann = await sync_to_async(member)("ann", await sync_to_async(Tenant.objects.create)(name="a"))
    company = await sync_to_async(skill)(ann.member.tenant, "house-style")
    server = _server(_calling("disable_skill", {"name": company.name}, []), skill_authoring=True)
    events = await _events(server, ann)
    assert not [e for e in events if e.get("name") == SKILLS_CHANGED_EVENT_NAME]
    await sync_to_async(company.refresh_from_db)()
    assert company.enabled is True


@pytest.mark.django_db(transaction=True)
async def test_a_deps_factory_without_the_request_fails_the_run_loudly() -> None:
    ann = await sync_to_async(member)("ann", await sync_to_async(Tenant.objects.create)(name="a"))
    server = _server(
        _calling("load_capability", {"id": "x"}, []),
        deps_factory=lambda request: AgentDeps(user=request.user),
    )
    events = await _events(server, ann)
    assert events[-1]["type"] == "RUN_ERROR"


def test_the_package_imports_before_the_app_registry_is_ready() -> None:
    code = "import django_ag_ui"
    env = {"DJANGO_SETTINGS_MODULE": "tests.conftest_settings", "PATH": ""}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
