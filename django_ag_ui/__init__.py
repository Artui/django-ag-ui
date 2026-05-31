"""Django ↔ Pydantic-AI ↔ AG-UI integration."""

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.urls import get_urls
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
    "DEFAULT_SYSTEM_PROMPT",
    "AppSettings",
    "AuditEvent",
    "AuditLogger",
    "DjangoAGUIView",
    "LoggingAuditLogger",
    "NullAuditLogger",
    "ToolBinding",
    "ToolCategory",
    "ToolRegistry",
    "ToolSpec",
    "X_CATEGORY_KEY",
    "X_DESTRUCTIVE_KEY",
    "__version__",
    "build_agent",
    "build_input_schema",
    "get_settings",
    "get_urls",
    "needs_confirmation",
    "resolve_audit_logger",
    "tool",
]
