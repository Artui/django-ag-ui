from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentSkill:
    """A progressively-disclosed skill the *agent* discovers and activates.

    The counterpart to :class:`~django_ag_ui.skills.types.skill_spec.SkillSpec`
    (a prompt the *human* picks from the ``/``-palette): only ``name`` +
    ``description`` are advertised to the model up front; the full
    ``instructions`` (and any scoped ``tools``) load when the model calls
    ``activate_skill`` — so a large skill library costs a couple of catalog
    lines per skill until one is actually used.

    Build them programmatically, or from ``SKILL.md`` directories via
    :func:`~django_ag_ui.skills.load_skill_directories.load_skill_directories`
    (the agentskills.io interop format).
    """

    name: str
    """Stable id the model activates by. Unique within a capability."""

    description: str
    """One-line summary in the up-front catalog — the model's only signal for
    when to activate, so make it say *when to use*, not just what it is."""

    instructions: str
    """The full skill body, injected into the **model context** (not the
    visible transcript) once activated."""

    tools: tuple[Callable[..., Any], ...] = field(default=())
    """Scoped tools that become callable only while the skill is active.
    Fully-typed callables, like registry tools (programmatic skills only —
    a ``SKILL.md`` cannot declare Python callables)."""

    resource_dir: Path | None = None
    """Directory of bundled resource files, readable by the model through
    ``read_skill_resource`` while the skill is active (path-traversal
    guarded). ``SKILL.md`` skills get their skill directory automatically."""

    def __post_init__(self) -> None:
        for field_name in ("name", "description", "instructions"):
            if not getattr(self, field_name):
                raise ValueError(f"AgentSkill.{field_name} must be a non-empty string")


__all__ = ["AgentSkill"]
