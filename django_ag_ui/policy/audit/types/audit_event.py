from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEvent:
    """A single tool invocation as seen by the audit logger.

    Arguments are stored as a string (typically JSON-encoded) to keep
    audit records cheap to serialize and to discourage retention of
    sensitive raw values.

    One run-level record rides this shape: when a client disconnects
    mid-run (cancel/stop), the view records ``tool_name="agent.run"`` with
    ``success=False`` and an ``error`` starting with ``"cancelled:"``, so
    cancelled runs are distinguishable in audit sinks without widening the
    ``AuditLogger`` protocol.
    """

    tool_name: str
    arguments_repr: str
    duration_ms: float
    success: bool
    error: str | None = None
    result_size: int | None = None


__all__ = ["AuditEvent"]
