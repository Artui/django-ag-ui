from __future__ import annotations

import logging

from django_ag_ui.policy.audit.types.audit_event import AuditEvent


class LoggingAuditLogger:
    """Writes audit events to the Python ``logging`` framework.

    Successful invocations log at ``INFO``; failures log at ``WARNING``
    with the error message included.
    """

    def __init__(self, logger_name: str = "django_ag_ui.audit") -> None:
        self._logger = logging.getLogger(logger_name)

    def record(self, event: AuditEvent) -> None:
        if event.success:
            self._logger.info(
                "tool=%s duration_ms=%.2f result_size=%s args=%s",
                event.tool_name,
                event.duration_ms,
                event.result_size,
                event.arguments_repr,
            )
        else:
            self._logger.warning(
                "tool=%s duration_ms=%.2f error=%s args=%s",
                event.tool_name,
                event.duration_ms,
                event.error,
                event.arguments_repr,
            )


__all__ = ["LoggingAuditLogger"]
