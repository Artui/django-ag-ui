from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSpec:
    """A pre-defined action offered to the user (a "skill").

    Serialised into the client catalog the frontend surfaces as chips and/or
    the ``/``-command palette.

    **A skill need not ship its prompt to the browser.** With ``prompt`` unset
    the catalog advertises only the name and label, and picking the skill sends
    the bare ``/name`` token — the agent resolves what that means, from the
    harness ``Skills`` capability or from its own instructions. That keeps the
    wording of an internal workflow on the server, where a catalog endpoint
    cannot leak it and a reader of the page source cannot lift it.

    Setting ``prompt`` keeps the older behaviour, where the client holds the
    text and fills any ``{placeholder}``s from its skill context before
    sending. Useful for a prompt that is genuinely a user-facing convenience
    rather than an internal one, and for placeholders only the page can fill.
    """

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
