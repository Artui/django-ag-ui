"""Django ↔ Pydantic-AI ↔ AG-UI integration."""

from django_ag_ui.agent.agent_factory import build_agent
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.agent.types.agent_factory_fn import AgentFactoryFn
from django_ag_ui.conf import AppSettings, get_settings
from django_ag_ui.constants import (
    X_CATEGORY_KEY,
    X_CONFIRM_KEY,
    X_DESTRUCTIVE_KEY,
    X_SUMMARY_KEY,
    ToolCategory,
)
from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_ag_ui.persistence.model_attachment_store import ModelAttachmentStore
from django_ag_ui.persistence.model_conversation_store import ModelConversationStore
from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.null_conversation_store import NullConversationStore
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.resolve_attachment_store import resolve_attachment_store
from django_ag_ui.persistence.resolve_conversation_store import resolve_conversation_store
from django_ag_ui.persistence.resolve_transcription_backend import resolve_transcription_backend
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.transcribe_view import TranscribeView
from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.attachment_store import AttachmentStore
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_meta import ConversationMeta
from django_ag_ui.persistence.types.conversation_store import ConversationStore
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend
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
from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.types.skill_spec import SkillSpec
from django_ag_ui.version import __version__

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "AGUIServer",
    "AgentConfig",
    "AgentFactoryFn",
    "AppSettings",
    "AttachmentRef",
    "AttachmentStore",
    "AttachmentsView",
    "AuditEvent",
    "AuditLogger",
    "Conversation",
    "ConversationMeta",
    "ConversationStore",
    "DjangoAGUIView",
    "DjangoSessionConversationStore",
    "LoggingAuditLogger",
    "ModelAttachmentStore",
    "ModelConversationStore",
    "NullAttachmentStore",
    "NullAuditLogger",
    "NullConversationStore",
    "NullTranscriptionBackend",
    "OpenedAttachment",
    "SkillRegistry",
    "SkillSpec",
    "ThreadsView",
    "ToolBinding",
    "ToolCategory",
    "ToolRegistry",
    "ToolSpec",
    "ToolsView",
    "TranscribeView",
    "TranscriptionBackend",
    "X_CATEGORY_KEY",
    "X_CONFIRM_KEY",
    "X_DESTRUCTIVE_KEY",
    "X_SUMMARY_KEY",
    "__version__",
    "build_agent",
    "build_input_schema",
    "get_settings",
    "needs_confirmation",
    "resolve_attachment_store",
    "resolve_audit_logger",
    "resolve_conversation_store",
    "resolve_transcription_backend",
    "tool",
]
