from __future__ import annotations

import json
from types import ModuleType
from typing import Any

import pytest
from ag_ui.core import AgentCapabilities
from django.test import override_settings
from django.urls import path
from django_spec_agents.contrib.store.default_attachment_store import DefaultAttachmentStore
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_inbound_queue import DefaultInboundQueue
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from django_ag_ui import AgentsDirectory
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedAsyncRequestFactory

pytestmark = pytest.mark.django_db(transaction=True)

_GUARDED = ToolGuardConfig(enabled=True)


def _factory(registry: ToolRegistry, config: Any) -> Agent[Any, Any]:
    return Agent(TestModel())


async def _capabilities(**kwargs: Any) -> dict[str, Any]:
    server = AGUIServer(
        ToolRegistry(),
        model=TestModel(),
        csrf_exempt=True,
        require_authenticated=False,
        id="caps",
        namespace="caps",
        **kwargs,
    )
    directory = AgentsDirectory([server], namespace="caps_dir")
    urlconf = ModuleType("caps_urls")
    urlconf.urlpatterns = [path("agent/", server.urls), path("home/", directory.urls)]
    agents = next(p for p in directory.urls[0] if p.name == "agents").callback
    with override_settings(ROOT_URLCONF=urlconf):
        response = await agents(AuthedAsyncRequestFactory().get("/"))
    (entry,) = json.loads(response.content)["agents"]
    return entry["capabilities"]


def _extras(model: BaseModel) -> list[str]:
    """Keys the SDK kept as extras (its models allow any), so a misspelt field shows."""
    found = list(model.model_extra or {})
    for value in vars(model).values():
        if isinstance(value, BaseModel):
            found += _extras(value)
    return found


async def test_capabilities_are_the_protocols_model_under_its_camel_case_wire_keys() -> None:
    capabilities = await _capabilities(
        name="Full",
        description="Every declaration on",
        conversation_store=DefaultConversationStore(),
        run_store=DefaultRunStore(),
        attachment_store=DefaultAttachmentStore(),
        inbound_queue=DefaultInboundQueue(),
        config=build_ag_ui_config(
            tool_guard=_GUARDED, forward_reasoning=False, run_timeout_seconds=90.5
        ),
    )

    assert capabilities == {
        "identity": {"name": "Full", "description": "Every declaration on"},
        "transport": {"streaming": True, "resumable": True},
        "tools": {"supported": True, "clientProvided": True},
        "multiAgent": {"supported": True},
        "reasoning": {"supported": False},
        "multimodal": {"input": {"file": True}},
        "execution": {"maxExecutionTime": 90500},
        "humanInTheLoop": {"supported": True, "approvals": True, "interrupts": True},
    }
    validated = AgentCapabilities.model_validate(capabilities)
    assert _extras(validated) == []
    assert validated.model_dump(mode="json", by_alias=True, exclude_none=True) == capabilities


async def test_a_bare_mount_declares_only_what_every_mount_knows() -> None:
    capabilities = await _capabilities(config=build_ag_ui_config(forward_reasoning=True))

    assert capabilities == {
        "transport": {"streaming": True, "resumable": False},
        "tools": {"supported": True, "clientProvided": True},
    }


@pytest.mark.parametrize(
    ("base", "change", "key", "before", "after"),
    [
        (
            {},
            {"run_store": DefaultRunStore()},
            "transport",
            {"streaming": True, "resumable": False},
            {"streaming": True, "resumable": True},
        ),
        (
            {"config": build_ag_ui_config()},
            {"config": build_ag_ui_config(tool_guard=_GUARDED)},
            "humanInTheLoop",
            None,
            {"supported": True, "approvals": True, "interrupts": True},
        ),
        (
            {"config": build_ag_ui_config(tool_guard=_GUARDED)},
            {"agent_factory": _factory},
            "humanInTheLoop",
            {"supported": True, "approvals": True, "interrupts": True},
            None,
        ),
        (
            {"config": build_ag_ui_config(forward_reasoning=True)},
            {"config": build_ag_ui_config(forward_reasoning=False)},
            "reasoning",
            None,
            {"supported": False},
        ),
        (
            {},
            {"attachment_store": DefaultAttachmentStore()},
            "multimodal",
            None,
            {"input": {"file": True}},
        ),
        ({}, {"inbound_queue": DefaultInboundQueue()}, "multiAgent", None, {"supported": True}),
        ({}, {"child_threads": lambda: None}, "multiAgent", None, {"supported": True}),
        (
            {"config": build_ag_ui_config(run_timeout_seconds=0)},
            {"config": build_ag_ui_config(run_timeout_seconds=0.25)},
            "execution",
            None,
            {"maxExecutionTime": 250},
        ),
        ({}, {"name": "Named"}, "identity", None, {"name": "Named"}),
        ({}, {"description": "Said"}, "identity", None, {"description": "Said"}),
    ],
    ids=[
        "run-store-resumable",
        "tool-guard-approvals",
        "agent-factory-drops-approvals",
        "reasoning-dropped",
        "attachments-file",
        "inbound-queue-multi-agent",
        "child-threads-multi-agent",
        "run-timeout-execution",
        "name-identity",
        "description-identity",
    ],
)
async def test_each_declaration_follows_its_mount_configuration(
    base: dict[str, Any], change: dict[str, Any], key: str, before: Any, after: Any
) -> None:
    assert (await _capabilities(**base)).get(key) == before
    assert (await _capabilities(**(base | change))).get(key) == after
