from __future__ import annotations

from typing import Protocol, runtime_checkable

from django_ag_ui.policy.audit.types.audit_event import AuditEvent


@runtime_checkable
class AuditLogger(Protocol):
    """Sink for tool-invocation records.

    Implementations are free to drop, sample, or forward events. The
    package ships a no-op default (``NullAuditLogger``) and a
    ``logging``-backed implementation (``LoggingAuditLogger``); projects
    supply their own by setting ``DJANGO_AG_UI["AUDIT_LOGGER"]`` to a
    dotted path.
    """

    def record(self, event: AuditEvent) -> None: ...


__all__ = ["AuditLogger"]
