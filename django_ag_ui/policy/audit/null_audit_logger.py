from __future__ import annotations

from django_ag_ui.policy.audit.types.audit_event import AuditEvent


class NullAuditLogger:
    """Discards every event. The default when no audit logger is configured."""

    def record(self, event: AuditEvent) -> None:
        return None


__all__ = ["NullAuditLogger"]
