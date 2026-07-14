from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolGuardConfig:
    """Resolved ``DJANGO_AG_UI['TOOL_GUARD']`` policy for the server-side gate.

    Turns the :class:`~django_ag_ui.policy.guard.tool_guard.ToolGuard`
    capability on and tunes *which* tools it flips to require human approval.
    **Off by default** (``enabled=False``): the gate never surprises a project
    that hasn't opted in — matching the package's "no server-side gate unless
    asked" posture.

    When ``enabled``, a tool is gated for approval unless its name is in
    ``exempt``, and it is gated when either it is destructive (a registry
    ``@tool(destructive=True)`` or a drf-mcp tool whose ``readOnlyHint`` is
    ``False``) or its name is in ``require_approval``. ``exempt`` wins over
    ``require_approval``.
    """

    enabled: bool = False
    """Whether the ``ToolGuard`` capability is composed into the agent."""

    exempt: frozenset[str] = field(default_factory=frozenset)
    """Tool names that are **never** gated, even if flagged destructive — the
    escape hatch for a mutation a project has decided is safe to auto-run."""

    require_approval: frozenset[str] = field(default_factory=frozenset)
    """Tool names that are **always** gated, even if not flagged destructive —
    force-approval for a read tool a project treats as sensitive. ``exempt``
    takes precedence if a name appears in both."""


__all__ = ["ToolGuardConfig"]
