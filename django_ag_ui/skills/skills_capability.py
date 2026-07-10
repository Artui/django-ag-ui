"""``SkillsCapability`` — progressive-disclosure skills for the agent."""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset, ToolsetTool

from django_ag_ui.skills.load_skill_directories import load_skill_directories
from django_ag_ui.skills.types.agent_skill import AgentSkill

_BUILTIN_TOOL_NAMES = frozenset({"search_skills", "activate_skill", "read_skill_resource"})


class SkillsCapability(AbstractCapability[Any]):
    """Exposes :class:`AgentSkill`\\ s to the agent via progressive disclosure.

    Up front the model sees only a catalog of skill names + descriptions (a
    couple of instruction lines per skill) and two tools — ``search_skills``
    and ``activate_skill``. Activating a skill injects its full instructions
    into the **model context** (never the visible transcript) and makes its
    scoped tools callable; ``read_skill_resource`` (registered when any skill
    bundles files) serves a skill's resource files, path-traversal guarded.

    Build it from programmatic skills, ``SKILL.md`` directories
    (agentskills.io), or both::

        capability = SkillsCapability(
            [AgentSkill(name="triage", description="...", instructions="...")],
            directories=[BASE_DIR / "skills"],
        )

    Hand it to the agent through ``AGUIServer(agent_skills=...)``,
    ``DJANGO_AG_UI["CAPABILITIES"]`` (a dotted path), or directly in
    ``AgentConfig.capabilities``. Activation state is **per run**
    (:meth:`for_run` isolation): each run starts with no skills active.
    """

    def __init__(
        self,
        skills: Sequence[AgentSkill] = (),
        *,
        directories: Sequence[str | Path] = (),
    ) -> None:
        loaded = [*skills, *load_skill_directories(directories)]
        if not loaded:
            raise ValueError("SkillsCapability needs at least one skill")
        self._skills: dict[str, AgentSkill] = {}
        for skill in loaded:
            if skill.name in self._skills:
                raise ValueError(f"skill {skill.name!r} registered twice")
            self._skills[skill.name] = skill
        _validate_tool_names(self._skills)
        # One FunctionToolset per skill with scoped tools, built once —
        # stateless; which of them is *exposed* is the per-run question.
        self._skill_toolsets: dict[str, FunctionToolset[Any]] = {
            name: FunctionToolset(list(skill.tools), id=f"skill:{name}")
            for name, skill in self._skills.items()
            if skill.tools
        }
        self._active: set[str] = set()

    async def for_run(self, ctx: RunContext[Any]) -> SkillsCapability:
        """A per-run clone with fresh activation state (skills stay shared)."""
        clone = copy.copy(self)
        clone._active = set()
        return clone

    def get_instructions(self) -> Callable[[], str]:
        # A zero-arg callable is re-evaluated on every model request, so a
        # mid-run activation reaches the model on its next request.
        return self._render_instructions

    def get_toolset(self) -> AbstractToolset[Any]:
        return _SkillToolset(self)

    def catalog(self) -> list[dict[str, Any]]:
        """Name + description entries for the GET skills catalog.

        Marked ``"agent": True`` so clients can tell them apart from the
        ``/``-palette prompt skills (which carry a ``prompt``; these never do).
        """
        return [
            {"name": skill.name, "description": skill.description, "agent": True}
            for skill in self._skills.values()
        ]

    def _render_instructions(self) -> str:
        lines = [
            "## Skills",
            "You have access to the following skills. Activate one with",
            "`activate_skill(name)` when its description matches the task;",
            "its full instructions (and any scoped tools) then become available.",
            "",
        ]
        lines.extend(f"- `{skill.name}`: {skill.description}" for skill in self._skills.values())
        for name in sorted(self._active):
            skill = self._skills[name]
            lines.extend(["", f"### Active skill: {name}", "", skill.instructions])
        return "\n".join(lines)

    def _search(self, query: str) -> list[dict[str, str]]:
        needle = query.strip().lower()
        return [
            {"name": skill.name, "description": skill.description}
            for skill in self._skills.values()
            if needle in skill.name.lower() or needle in skill.description.lower()
        ]

    def _activate(self, name: str) -> str:
        skill = self._skills.get(name)
        if skill is None:
            raise ModelRetry(f"unknown skill {name!r}; available: {sorted(self._skills)}")
        if name in self._active:
            return f"Skill {name!r} is already active."
        self._active.add(name)
        notes = ["its instructions are now in your context"]
        if skill.tools:
            notes.append(f"its tools are now callable: {[_tool_name(fn) for fn in skill.tools]}")
        if skill.resource_dir is not None:
            notes.append("its resource files are readable via read_skill_resource")
        return f"Activated skill {name!r}; " + "; ".join(notes) + "."

    def _read_resource(self, skill_name: str, path: str) -> str:
        skill = self._skills.get(skill_name)
        if skill is None:
            raise ModelRetry(f"unknown skill {skill_name!r}; available: {sorted(self._skills)}")
        if skill_name not in self._active:
            raise ModelRetry(f"skill {skill_name!r} is not active; call activate_skill first")
        if skill.resource_dir is None:
            raise ModelRetry(f"skill {skill_name!r} bundles no resource files")
        base = skill.resource_dir.resolve()
        target = (base / path).resolve()
        # The traversal guard: whatever ``path`` contains (.., absolute paths,
        # symlink hops), the resolved target must stay inside the skill dir.
        if target == base or not target.is_relative_to(base):
            raise ModelRetry(f"path {path!r} is outside the skill's resource directory")
        if not target.is_file():
            raise ModelRetry(f"skill {skill_name!r} has no resource file {path!r}")
        return target.read_text(encoding="utf-8")


class _SkillToolset(AbstractToolset[Any]):
    """The capability's tool surface: built-ins + the *active* skills' tools."""

    def __init__(self, capability: SkillsCapability) -> None:
        self._capability = capability
        self._builtin = _build_builtin_toolset(capability)

    @property
    def id(self) -> str | None:
        return "skills"

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        tools = dict(await self._builtin.get_tools(ctx))
        for name in sorted(self._capability._active):
            toolset = self._capability._skill_toolsets.get(name)
            if toolset is not None:
                tools.update(await toolset.get_tools(ctx))
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        # Each ToolsetTool carries the FunctionToolset that produced it —
        # delegate there, whichever routing convention the run loop uses.
        return await tool.toolset.call_tool(name, tool_args, ctx, tool)


def _build_builtin_toolset(capability: SkillsCapability) -> FunctionToolset[Any]:
    def search_skills(query: str = "") -> list[dict[str, str]]:
        """Search the available skills by name/description substring.

        An empty query lists every skill.
        """
        return capability._search(query)

    def activate_skill(name: str) -> str:
        """Activate a skill, loading its full instructions and scoped tools."""
        return capability._activate(name)

    tools: list[Callable[..., Any]] = [search_skills, activate_skill]
    if any(skill.resource_dir is not None for skill in capability._skills.values()):

        def read_skill_resource(skill: str, path: str) -> str:
            """Read a resource file bundled with an **active** skill.

            ``path`` is relative to the skill's resource directory.
            """
            return capability._read_resource(skill, path)

        tools.append(read_skill_resource)
    return FunctionToolset(tools, id="skills")


def _tool_name(fn: Callable[..., Any]) -> str:
    """The name a scoped tool is advertised under (what ``FunctionToolset`` uses).

    Skill tools are plain functions by convention, but the annotation is
    ``Callable`` — read the attribute dynamically for the checker's sake.
    """
    return str(getattr(fn, "__name__", fn))


def _validate_tool_names(skills: dict[str, AgentSkill]) -> None:
    """Fail fast on scoped-tool name collisions (two skills may be active at once)."""
    owners: dict[str, str] = {}
    for skill in skills.values():
        for fn in skill.tools:
            tool_name = _tool_name(fn)
            if tool_name in _BUILTIN_TOOL_NAMES:
                raise ValueError(
                    f"skill {skill.name!r} tool {tool_name!r} shadows a built-in skill tool"
                )
            if tool_name in owners:
                raise ValueError(
                    f"tool {tool_name!r} is declared by both {owners[tool_name]!r} "
                    f"and {skill.name!r}"
                )
            owners[tool_name] = skill.name
    return None


__all__ = ["SkillsCapability"]
