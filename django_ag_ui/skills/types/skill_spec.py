from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSpec:
    """A pre-defined action offered to the user (a "skill")."""

    name: str
    """Stable id; the ``/token`` in the palette. Unique within a registry."""

    title: str
    """Label shown in chips and the palette."""

    prompt: str | None = None
    """Prompt text handed to the client, or ``None`` to keep it server-side and
    have the client send ``/name`` instead. May contain ``{placeholder}``s the
    client fills before sending."""

    description: str | None = None
    """Optional secondary line shown in the palette."""

    send_immediately: bool = False
    """Send on pick instead of pre-filling the input. Surfaced as
    ``sendImmediately``."""

    chip: bool = False
    """Also surface as a chip (the palette shows all skills regardless)."""


__all__ = ["SkillSpec"]
