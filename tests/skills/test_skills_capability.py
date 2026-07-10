from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from django_ag_ui.skills.skills_capability import SkillsCapability, _SkillToolset
from django_ag_ui.skills.types.agent_skill import AgentSkill

INSTRUCTIONS_BODY = "SECRET-BODY: when triaging, always check the backlog first."


def _lookup(ticket: str) -> str:
    """Look a ticket up."""
    return f"ticket:{ticket}"


def _triage_skill(**overrides: object) -> AgentSkill:
    defaults: dict[str, object] = {
        "name": "triage",
        "description": "Triage an incoming bug report.",
        "instructions": INSTRUCTIONS_BODY,
        "tools": (_lookup,),
    }
    defaults.update(overrides)
    return AgentSkill(**defaults)  # type: ignore[arg-type]


# --- construction ------------------------------------------------------------


def test_needs_at_least_one_skill() -> None:
    with pytest.raises(ValueError, match="at least one skill"):
        SkillsCapability()


def test_duplicate_skill_names_rejected() -> None:
    with pytest.raises(ValueError, match="registered twice"):
        SkillsCapability([_triage_skill(), _triage_skill()])


def test_skill_tool_shadowing_builtin_rejected() -> None:
    def activate_skill() -> str:
        """Impostor."""
        return ""

    with pytest.raises(ValueError, match="shadows a built-in"):
        SkillsCapability([_triage_skill(tools=(activate_skill,))])


def test_cross_skill_tool_collision_rejected() -> None:
    other = _triage_skill(name="other", tools=(_lookup,))
    with pytest.raises(ValueError, match="declared by both"):
        SkillsCapability([_triage_skill(), other])


def test_agent_skill_requires_non_empty_fields() -> None:
    with pytest.raises(ValueError, match="description"):
        AgentSkill(name="x", description="", instructions="y")


def test_directories_compose_with_programmatic_skills(tmp_path: Path) -> None:
    skill_dir = tmp_path / "review"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Review code.\n---\nReview carefully.\n", encoding="utf-8"
    )
    capability = SkillsCapability([_triage_skill()], directories=[tmp_path])
    names = {entry["name"] for entry in capability.catalog()}
    assert names == {"triage", "review"}


# --- catalog + instructions ---------------------------------------------------


def test_catalog_entries_are_promptless_and_marked_agent() -> None:
    capability = SkillsCapability([_triage_skill()])
    (entry,) = capability.catalog()
    assert entry == {
        "name": "triage",
        "description": "Triage an incoming bug report.",
        "agent": True,
    }


def test_instructions_list_catalog_but_not_bodies_until_active() -> None:
    capability = SkillsCapability([_triage_skill()])
    rendered = capability.get_instructions()()
    assert "`triage`" in rendered
    assert "Triage an incoming bug report." in rendered
    assert INSTRUCTIONS_BODY not in rendered

    capability._activate("triage")
    rendered = capability.get_instructions()()
    assert INSTRUCTIONS_BODY in rendered


# --- activation + search ------------------------------------------------------


def test_activate_unknown_skill_is_model_retry() -> None:
    capability = SkillsCapability([_triage_skill()])
    with pytest.raises(ModelRetry, match="unknown skill"):
        capability._activate("nope")


def test_activate_twice_reports_already_active() -> None:
    capability = SkillsCapability([_triage_skill()])
    assert "Activated" in capability._activate("triage")
    assert "already active" in capability._activate("triage")


def test_activation_message_mentions_tools_and_resources(tmp_path: Path) -> None:
    capability = SkillsCapability([_triage_skill(resource_dir=tmp_path)])
    message = capability._activate("triage")
    assert "_lookup" in message
    assert "read_skill_resource" in message


def test_search_matches_name_and_description_case_insensitively() -> None:
    capability = SkillsCapability([_triage_skill()])
    assert capability._search("TRIAGE") == [
        {"name": "triage", "description": "Triage an incoming bug report."}
    ]
    assert capability._search("bug report") != []
    assert capability._search("unrelated") == []
    # Empty query lists everything.
    assert len(capability._search("")) == 1


def test_for_run_isolates_activation_state() -> None:
    capability = SkillsCapability([_triage_skill()])
    capability._active.add("triage")
    import asyncio

    clone = asyncio.run(capability.for_run(None))
    assert clone._active == set()
    assert clone._skills is capability._skills


# --- resource reads -----------------------------------------------------------


def _resourceful(tmp_path: Path) -> SkillsCapability:
    (tmp_path / "notes.txt").write_text("resource-content", encoding="utf-8")
    capability = SkillsCapability([_triage_skill(resource_dir=tmp_path)])
    capability._activate("triage")
    return capability


def test_read_resource_returns_file_content(tmp_path: Path) -> None:
    capability = _resourceful(tmp_path)
    assert capability._read_resource("triage", "notes.txt") == "resource-content"


def test_read_resource_requires_known_skill(tmp_path: Path) -> None:
    capability = _resourceful(tmp_path)
    with pytest.raises(ModelRetry, match="unknown skill"):
        capability._read_resource("nope", "notes.txt")


def test_read_resource_requires_activation(tmp_path: Path) -> None:
    capability = SkillsCapability([_triage_skill(resource_dir=tmp_path)])
    with pytest.raises(ModelRetry, match="not active"):
        capability._read_resource("triage", "notes.txt")


def test_read_resource_without_resource_dir_is_model_retry() -> None:
    capability = SkillsCapability([_triage_skill()])
    capability._activate("triage")
    with pytest.raises(ModelRetry, match="no resource files"):
        capability._read_resource("triage", "anything.txt")


@pytest.mark.parametrize("escape", ["../outside.txt", "/etc/passwd", "."])
def test_read_resource_traversal_is_blocked(tmp_path: Path, escape: str) -> None:
    base = tmp_path / "skill"
    base.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    capability = SkillsCapability([_triage_skill(resource_dir=base)])
    capability._activate("triage")
    with pytest.raises(ModelRetry, match="outside the skill"):
        capability._read_resource("triage", escape)


def test_read_resource_missing_file_is_model_retry(tmp_path: Path) -> None:
    capability = _resourceful(tmp_path)
    with pytest.raises(ModelRetry, match="no resource file"):
        capability._read_resource("triage", "missing.txt")


# --- full agent-run integration ------------------------------------------------
# The plan's verify: a skill activates mid-run, its scoped tools become
# callable, and its instructions reach the *model* context without ever
# entering the visible transcript.


async def test_agent_run_activates_skill_and_calls_scoped_tool() -> None:
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    seen: dict[str, object] = {}

    def model_fn(messages: list, info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        step = seen.setdefault("step", 0)
        assert isinstance(step, int)
        seen["step"] = step + 1
        tool_names = {tool.name for tool in info.function_tools}
        if step == 0:
            # Progressive disclosure: catalog advertised, body hidden, scoped
            # tool not yet callable.
            seen["initial_instructions"] = request.instructions
            seen["initial_tools"] = tool_names
            return ModelResponse(
                parts=[ToolCallPart(tool_name="activate_skill", args={"name": "triage"})]
            )
        if step == 1:
            # Post-activation: instructions carry the body, the scoped tool
            # is callable.
            seen["active_instructions"] = request.instructions
            seen["active_tools"] = tool_names
            return ModelResponse(parts=[ToolCallPart(tool_name="_lookup", args={"ticket": "T-1"})])
        return ModelResponse(parts=[TextPart("done")])

    capability = SkillsCapability([_triage_skill()])
    agent = Agent(FunctionModel(model_fn), capabilities=[capability])
    result = await agent.run("triage T-1")

    assert result.output == "done"
    initial_instructions = str(seen["initial_instructions"])
    assert "`triage`" in initial_instructions
    assert INSTRUCTIONS_BODY not in initial_instructions
    assert "_lookup" not in seen["initial_tools"]  # type: ignore[operator]
    assert INSTRUCTIONS_BODY in str(seen["active_instructions"])
    assert "_lookup" in seen["active_tools"]  # type: ignore[operator]
    # Instructions ride the model context only — never the message parts the
    # client renders.
    for message in result.all_messages():
        for part in getattr(message, "parts", []):
            content = getattr(part, "content", "")
            assert INSTRUCTIONS_BODY not in str(content)
    # The base capability's state is untouched (per-run isolation).
    assert capability._active == set()


def _ctx() -> object:
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    return RunContext(deps=None, model=TestModel(), usage=RunUsage())


async def test_skill_toolset_call_tool_delegates_to_owning_toolset() -> None:
    ctx = _ctx()
    capability = SkillsCapability([_triage_skill()])
    toolset = _SkillToolset(capability)
    assert toolset.id == "skills"
    tools = await toolset.get_tools(ctx)
    found = await toolset.call_tool(
        "search_skills", {"query": "triage"}, ctx, tools["search_skills"]
    )
    assert found == [{"name": "triage", "description": "Triage an incoming bug report."}]
    result = await toolset.call_tool(
        "activate_skill", {"name": "triage"}, ctx, tools["activate_skill"]
    )
    assert "Activated" in result
    # The scoped tool appears once active, and routes to its own toolset.
    tools = await toolset.get_tools(ctx)
    assert "_lookup" in tools
    assert await toolset.call_tool("_lookup", {"ticket": "T-9"}, ctx, tools["_lookup"]) == (
        "ticket:T-9"
    )


async def test_read_skill_resource_registers_and_routes_when_resources_exist(
    tmp_path: Path,
) -> None:
    ctx = _ctx()
    (tmp_path / "notes.txt").write_text("resource-content", encoding="utf-8")
    capability = SkillsCapability([_triage_skill(resource_dir=tmp_path)])
    capability._activate("triage")
    toolset = _SkillToolset(capability)
    tools = await toolset.get_tools(ctx)
    content = await toolset.call_tool(
        "read_skill_resource",
        {"skill": "triage", "path": "notes.txt"},
        ctx,
        tools["read_skill_resource"],
    )
    assert content == "resource-content"


async def test_tool_less_active_skill_contributes_no_toolset() -> None:
    # A skill without scoped tools activates fine (instructions only) and the
    # merged tool surface stays the built-ins.
    ctx = _ctx()
    capability = SkillsCapability([_triage_skill(tools=())])
    message = capability._activate("triage")
    assert "instructions are now in your context" in message
    assert "callable" not in message
    toolset = _SkillToolset(capability)
    assert set(await toolset.get_tools(ctx)) == {"search_skills", "activate_skill"}
