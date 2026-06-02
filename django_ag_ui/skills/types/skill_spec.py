from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSpec:
    """A pre-defined prompt offered to the user (a "skill").

    Serialised into the client catalog the frontend surfaces as chips and/or
    the ``/``-command palette. Skills are data, not callables — the ``prompt``
    is a static string (it may contain ``{placeholder}``s the client fills from
    its skill context before sending).
    """

    name: str
    """Stable id; the ``/token`` in the palette. Unique within a registry."""

    title: str
    """Label shown in chips and the palette."""

    prompt: str
    """The prompt inserted (or sent). May contain ``{placeholder}``s."""

    description: str | None = None
    """Optional secondary line shown in the palette."""

    send_immediately: bool = False
    """Send on pick instead of pre-filling the input. Surfaced as
    ``sendImmediately``."""

    chip: bool = False
    """Also surface as a chip (the palette shows all skills regardless)."""


__all__ = ["SkillSpec"]
