from __future__ import annotations

import pytest

from django_ag_ui.policy.audit.logging_audit_logger import LoggingAuditLogger
from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger
from django_ag_ui.policy.audit.resolve_audit_logger import resolve_audit_logger


def test_none_returns_null_logger() -> None:
    assert isinstance(resolve_audit_logger(None), NullAuditLogger)


def test_valid_dotted_path_loads_class() -> None:
    logger = resolve_audit_logger(
        "django_ag_ui.policy.audit.logging_audit_logger.LoggingAuditLogger"
    )
    assert isinstance(logger, LoggingAuditLogger)


def test_invalid_path_raises() -> None:
    with pytest.raises(ValueError, match="invalid audit logger path"):
        resolve_audit_logger("LoggingAuditLogger")


def test_non_logger_class_rejected() -> None:
    with pytest.raises(TypeError, match="AuditLogger"):
        resolve_audit_logger("collections.OrderedDict")
