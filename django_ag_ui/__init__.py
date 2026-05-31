"""Django ↔ Pydantic-AI ↔ AG-UI integration."""

from django_ag_ui.conf import AppSettings, get_settings
from django_ag_ui.constants import (
    X_CATEGORY_KEY,
    X_DESTRUCTIVE_KEY,
    ToolCategory,
)
from django_ag_ui.policy.audit.logging_audit_logger import LoggingAuditLogger
from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger
from django_ag_ui.policy.audit.resolve_audit_logger import resolve_audit_logger
from django_ag_ui.policy.audit.types.audit_event import AuditEvent
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger
from django_ag_ui.policy.auto_confirm import needs_confirmation
from django_ag_ui.registry.build_input_schema import build_input_schema
from django_ag_ui.registry.decorator import tool
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.registry.types.tool_binding import ToolBinding
from django_ag_ui.registry.types.tool_spec import ToolSpec
from django_ag_ui.version import __version__

__all__ = [
    "AppSettings",
    "AuditEvent",
    "AuditLogger",
    "LoggingAuditLogger",
    "NullAuditLogger",
    "ToolBinding",
    "ToolCategory",
    "ToolRegistry",
    "ToolSpec",
    "X_CATEGORY_KEY",
    "X_DESTRUCTIVE_KEY",
    "__version__",
    "build_input_schema",
    "get_settings",
    "needs_confirmation",
    "resolve_audit_logger",
    "tool",
]
