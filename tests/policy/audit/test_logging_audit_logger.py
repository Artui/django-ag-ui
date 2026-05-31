from __future__ import annotations

import logging

from django_ag_ui.policy.audit.logging_audit_logger import LoggingAuditLogger
from django_ag_ui.policy.audit.types.audit_event import AuditEvent


def test_success_logs_at_info(caplog) -> None:  # noqa: ANN001
    logger = LoggingAuditLogger("test_logger.success")
    caplog.set_level(logging.INFO, logger="test_logger.success")
    logger.record(
        AuditEvent(
            tool_name="ping",
            arguments_repr='{"a": 1}',
            duration_ms=12.34,
            success=True,
            result_size=100,
        ),
    )
    assert any(r.levelno == logging.INFO and "tool=ping" in r.message for r in caplog.records)


def test_failure_logs_at_warning(caplog) -> None:  # noqa: ANN001
    logger = LoggingAuditLogger("test_logger.failure")
    caplog.set_level(logging.WARNING, logger="test_logger.failure")
    logger.record(
        AuditEvent(
            tool_name="boom",
            arguments_repr="{}",
            duration_ms=1.0,
            success=False,
            error="ValueError: kaboom",
        ),
    )
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "boom" in warning.message
    assert "ValueError" in warning.message
