from __future__ import annotations

from ag_ui.core import CustomEvent

from django_ag_ui.agent.event_timestamp import event_timestamp

SKILLS_CHANGED_EVENT_NAME = "ag_ui.skills_changed"
"""``name`` of the ``CUSTOM`` event a run sends when it saved a stored skill.

The page refetches its skill catalog on it. The value is ``{"name": <skill name>}``,
never a body: the catalog the page refetches is what decides what it may see.
"""


def skills_changed(name: str) -> CustomEvent:
    """The event announcing that the stored skill ``name`` was written."""
    return CustomEvent(
        name=SKILLS_CHANGED_EVENT_NAME, value={"name": name}, timestamp=event_timestamp()
    )


__all__ = ["SKILLS_CHANGED_EVENT_NAME", "skills_changed"]
