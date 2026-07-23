from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from django_ag_ui.skills.types.skill_spec import SkillSpec


class SkillRegistry:
    """An ordered, named collection of :class:`SkillSpec`s.

    State lives on the instance (like :class:`~django_pydantic_agent.registry.tool_registry.ToolRegistry`).
    :meth:`payload` produces the JSON-serialisable catalog the frontend consumes
    (camelCase keys, optional fields omitted when default).
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec) -> SkillSpec:
        """Register ``spec``; raise ``ValueError`` if the name is taken."""
        if spec.name in self._skills:
            raise ValueError(f"skill {spec.name!r} already registered")
        self._skills[spec.name] = spec
        return spec

    def add(
        self,
        name: str,
        title: str,
        prompt: str,
        *,
        description: str | None = None,
        send_immediately: bool = False,
        chip: bool = False,
    ) -> SkillSpec:
        """Construct a :class:`SkillSpec` and register it (convenience)."""
        return self.register(
            SkillSpec(
                name=name,
                title=title,
                prompt=prompt,
                description=description,
                send_immediately=send_immediately,
                chip=chip,
            ),
        )

    def __iter__(self) -> Iterator[SkillSpec]:
        return iter(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    def payload(self) -> list[dict[str, Any]]:
        """The client catalog: a list of skill dicts with camelCase keys."""
        return [_as_dict(spec) for spec in self._skills.values()]


def _as_dict(spec: SkillSpec) -> dict[str, Any]:
    data: dict[str, Any] = {"name": spec.name, "title": spec.title, "prompt": spec.prompt}
    if spec.description is not None:
        data["description"] = spec.description
    if spec.send_immediately:
        data["sendImmediately"] = True
    if spec.chip:
        data["chip"] = True
    return data


__all__ = ["SkillRegistry"]
