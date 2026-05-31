from __future__ import annotations

from django_ag_ui.registry.types.tool_binding import ToolBinding


def needs_confirmation(binding: ToolBinding, *, auto_confirm: bool) -> bool:
    """Return whether ``binding`` should prompt for confirmation.

    A tool needs confirmation when it is destructive and the project has
    not opted into autopilot (``auto_confirm=False``). The actual modal
    is rendered client-side; this helper is the canonical server-side
    statement of the rule, used to annotate tool listings and to keep the
    confirmation policy in one place.
    """
    return binding.spec.destructive and not auto_confirm


__all__ = ["needs_confirmation"]
