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

    organization_id: str | None = None
    """Multi-tenant scope of the acting user, when the sink can derive one.
    ``None`` at this layer — a custom :class:`AuditLogger` fills it from its
    own tenancy model."""

    target_type: str | None = None
    """Kind of domain object the call acted on (``"order"``, ``"user"``, …).
    ``None`` at this layer — tool args are domain-opaque here; a custom sink
    that knows its tools can classify them."""

    target_id: str | None = None
    """Identifier of the acted-on object, paired with ``target_type``."""

    ip_address: str | None = None
    """Client IP of the request that drove the run, when the view knows it."""


__all__ = ["AuditEvent"]
