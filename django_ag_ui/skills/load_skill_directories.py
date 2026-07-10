"""Load :class:`AgentSkill`\\ s from ``SKILL.md`` directories (agentskills.io)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from django_ag_ui.skills.types.agent_skill import AgentSkill


def load_skill_directories(directories: Sequence[str | Path]) -> list[AgentSkill]:
    """Load skills from directories of ``<skill-name>/SKILL.md`` bundles.

    Each ``directory`` holds one sub-directory per skill; a sub-directory is a
    skill when it contains a ``SKILL.md`` (others — shared assets, VCS noise —
    are skipped). The file follows the agentskills.io convention: a
    ``---``-fenced frontmatter of flat ``key: value`` lines (``name`` optional,
    defaulting to the directory name; ``description`` required) followed by the
    skill's instructions as the body. The skill directory itself becomes the
    skill's ``resource_dir``, so bundled files are readable via
    ``read_skill_resource`` once the skill is active.

    Skills load in sorted directory order, so catalogs are stable across
    filesystems. A database-backed loader is deliberately not shipped — build
    the ``AgentSkill``\\ s from your own storage and pass them to
    :class:`~django_ag_ui.skills.skills_capability.SkillsCapability` directly.
    """
    skills: list[AgentSkill] = []
    for directory in directories:
        base = Path(directory)
        if not base.is_dir():
            raise ValueError(f"skill directory {str(base)!r} does not exist")
        for skill_dir in sorted(child for child in base.iterdir() if child.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                skills.append(_parse_skill_md(skill_md, skill_dir))
    return skills


def _parse_skill_md(skill_md: Path, skill_dir: Path) -> AgentSkill:
    frontmatter, body = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
    description = frontmatter.get("description")
    if not description:
        raise ValueError(f"{skill_md}: frontmatter must declare a description")
    if not body.strip():
        raise ValueError(f"{skill_md}: the instructions body is empty")
    return AgentSkill(
        name=frontmatter.get("name") or skill_dir.name,
        description=description,
        instructions=body.strip(),
        resource_dir=skill_dir,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a ``---``-fenced frontmatter block off ``text``.

    Flat ``key: value`` lines only (the agentskills.io required fields are
    flat); anything else in the block is ignored. Text without a leading
    fence is all body.
    """
    if not text.startswith("---\n"):
        return {}, text
    rest = text[len("---\n") :]
    fence = rest.find("\n---")
    if fence < 0:
        return {}, text
    frontmatter: dict[str, str] = {}
    for line in rest[:fence].splitlines():
        key, separator, value = line.partition(":")
        if separator and value.strip():
            frontmatter[key.strip()] = value.strip()
    body = rest[fence + len("\n---") :]
    return frontmatter, body.lstrip("\n")


__all__ = ["load_skill_directories"]
