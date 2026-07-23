"""Django ↔ Pydantic-AI ↔ AG-UI integration."""

from django_pydantic_agent import (
    X_CATEGORY_KEY,
    X_CONFIRM_KEY,
    X_DESTRUCTIVE_KEY,
    X_SUMMARY_KEY,
    AgentConfig,
    AgentFactoryFn,
    AttachmentRef,
    AttachmentStore,
    AuditCapability,
    AuditEvent,
    AuditLogger,
    Conversation,
    ConversationMeta,
    ConversationStore,
    DjangoSessionConversationStore,
    LoggingAuditLogger,
    ModelAttachmentStore,
    ModelConversationStore,
    NullAttachmentStore,
    NullAuditLogger,
    NullConversationStore,
    OpenedAttachment,
    ScopedConversationStore,
    ToolBinding,
    ToolCategory,
    ToolRegistry,
    ToolSpec,
    build_agent,
    build_input_schema,
    tool,
)

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.transcribe_view import TranscribeView
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend
from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.types.skill_spec import SkillSpec
from django_ag_ui.version import __version__

# The block imported from ``django_pydantic_agent`` above is re-exported
# **permanently**: those symbols moved into the shared agent-host substrate, but
# ``from django_ag_ui import ToolRegistry`` (and friends) keeps working for good,
# so downstream projects never have to chase the move. The public surface below
# is unchanged from before the extraction.
__all__ = [
    "AGUIConfig",
    "DEFAULT_SYSTEM_PROMPT",
    "AGUIServer",
    "AgentConfig",
    "AgentFactoryFn",
    "AgentSession",
    "AttachmentRef",
    "AttachmentStore",
    "AttachmentsView",
    "AuditCapability",
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
    "ScopedConversationStore",
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
    "build_ag_ui_config",
    "build_agent",
    "build_input_schema",
    "tool",
]
