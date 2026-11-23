from __future__ import annotations

import json
from types import ModuleType
from typing import Any

import pytest
from ag_ui.core import (
    AgentCapabilities,
    HumanInTheLoopCapabilities,
    MultiAgentCapabilities,
    SubagentInfo,
)
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
from pydantic_core import PydanticSerializationError

from django_ag_ui import AgentsDirectory
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedAsyncRequestFactory

pytestmark = pytest.mark.django_db(transaction=True)

_GUARDED = ToolGuardConfig(enabled=True)


def _factory(registry: ToolRegistry, config: Any) -> Agent[Any, Any]:
    return Agent(TestModel())


class DelegatingServer(AGUIServer):
    def agent_capabilities(self) -> AgentCapabilities:
        declared = super().agent_capabilities()
        multi_agent = declared.multi_agent or MultiAgentCapabilities()
        researcher = SubagentInfo(name="researcher", description="Finds sources")
        update = {"supported": True, "subagents": [researcher]}
        return declared.model_copy(update={"multi_agent": multi_agent.model_copy(update=update)})


async def _capabilities(
    server_class: type[AGUIServer] = AGUIServer, **kwargs: Any
) -> dict[str, Any]:
    server = server_class(
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
            {"streaming": True, "resumable": False},
        ),
        (
            {},
            {"config": build_ag_ui_config(run_timeout_seconds=30)},
            "transport",
            {"streaming": True, "resumable": False},
            {"streaming": True, "resumable": False},
        ),
        (
            {"run_store": DefaultRunStore()},
            {"config": build_ag_ui_config(run_timeout_seconds=30)},
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
        (
            {"attachment_store": DefaultAttachmentStore()},
            {"config": build_ag_ui_config(attachment_allowed_types=["image/png"])},
            "multimodal",
            {"input": {"file": True}},
            None,
        ),
        (
            {},
            {"config": build_ag_ui_config(attachment_allowed_types=["image/png"])},
            "multimodal",
            None,
            None,
        ),
        ({}, {"inbound_queue": DefaultInboundQueue()}, "multiAgent", None, {"supported": True}),
        ({}, {"child_threads": lambda: None}, "multiAgent", None, None),
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
        "run-store-without-timeout-is-not-resumable",
        "run-timeout-without-store-is-not-resumable",
        "run-store-with-timeout-resumable",
        "tool-guard-approvals",
        "agent-factory-drops-approvals",
        "reasoning-dropped",
        "attachments-file",
        "allowlist-drops-file",
        "allowlist-without-store-declares-nothing",
        "inbound-queue-multi-agent",
        "child-threads-do-not-declare-multi-agent",
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


@pytest.mark.parametrize(
    "mount", [{}, {"inbound_queue": DefaultInboundQueue()}], ids=["bare", "inbound-queue"]
)
async def test_a_subclass_override_is_what_the_directory_serves(mount: dict[str, Any]) -> None:
    capabilities = await _capabilities(DelegatingServer, name="Delegating", **mount)

    assert capabilities == {
        "identity": {"name": "Delegating"},
        "transport": {"streaming": True, "resumable": False},
        "tools": {"supported": True, "clientProvided": True},
        "multiAgent": {
            "supported": True,
            "subagents": [{"name": "researcher", "description": "Finds sources"}],
        },
    }
    assert _extras(AgentCapabilities.model_validate(capabilities)) == []


class _CopyingGuardServer(AGUIServer):
    def agent_capabilities(self) -> AgentCapabilities:
        declared = super().agent_capabilities()
        hitl = declared.human_in_the_loop or HumanInTheLoopCapabilities()
        update = {"human_in_the_loop": hitl.model_copy(update={"approve_with_edits": True})}
        return declared.model_copy(update=update)


class _ReplacingGuardServer(AGUIServer):
    def agent_capabilities(self) -> AgentCapabilities:
        hitl = HumanInTheLoopCapabilities(approve_with_edits=True)
        return super().agent_capabilities().model_copy(update={"human_in_the_loop": hitl})


class _DictServer(AGUIServer):
    def agent_capabilities(self) -> AgentCapabilities:
        update = {"multi_agent": {"supported": True}}
        return super().agent_capabilities().model_copy(update=update)


class _MisspeltServer(AGUIServer):
    def agent_capabilities(self) -> AgentCapabilities:
        multi_agent = MultiAgentCapabilities(supported=True, sub_agents=[{"name": "researcher"}])
        return super().agent_capabilities().model_copy(update={"multi_agent": multi_agent})


@pytest.mark.parametrize(
    ("server_class", "served"),
    [
        (
            _CopyingGuardServer,
            {"supported": True, "approvals": True, "interrupts": True, "approveWithEdits": True},
        ),
        (_ReplacingGuardServer, {"approveWithEdits": True}),
    ],
    ids=["copied-keeps-the-guard", "replaced-loses-the-guard"],
)
async def test_an_override_update_replaces_a_sub_model_whole(
    server_class: type[AGUIServer], served: dict[str, Any]
) -> None:
    config = build_ag_ui_config(tool_guard=_GUARDED)
    capabilities = await _capabilities(server_class, config=config)

    assert capabilities["humanInTheLoop"] == served


@pytest.mark.filterwarnings("ignore::UserWarning")
async def test_an_override_updating_with_a_dict_fails_to_serialise() -> None:
    with pytest.raises(PydanticSerializationError):
        await _capabilities(_DictServer)


async def test_an_override_spelling_sub_agents_is_served_under_the_wrong_key() -> None:
    capabilities = await _capabilities(_MisspeltServer)

    assert capabilities["multiAgent"] == {"supported": True, "sub_agents": [{"name": "researcher"}]}
    assert _extras(AgentCapabilities.model_validate(capabilities)) == ["sub_agents"]
