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
    ToolFailureConfig,
    ToolFailurePolicy,
    ToolGuard,
    ToolGuardConfig,
    ToolRegistry,
    ToolSpec,
    build_agent,
    build_input_schema,
    tool,
)

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.chart_activity import CHART_ACTIVITY_TYPE, chart_activity
from django_ag_ui.agent.chart_limits import MAX_LABELS, MAX_MAGNITUDE, MAX_POINTS
from django_ag_ui.agent.chart_points_delta import chart_points_delta
from django_ag_ui.agent.compaction_observer import CompactionObserver
from django_ag_ui.agent.fixed_window_throttle import FixedWindowThrottle
from django_ag_ui.agent.inject_compaction_events import COMPACTION_ACTIVITY_TYPE
from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT
from django_ag_ui.agent.tools_view import ToolsView
from django_ag_ui.agent.types.chart_kind import ChartKind
from django_ag_ui.agent.types.chart_series import ChartSeries
from django_ag_ui.agent.types.chart_spec import ChartSpec
from django_ag_ui.agent.types.throttle import Throttle
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.config.types.run_context_config import RunContextConfig
from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.scoped_step_store import ScopedStepStore
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.transcribe_view import TranscribeView
from django_ag_ui.persistence.types.thread_activity import ThreadActivity
from django_ag_ui.persistence.types.thread_activity_source import ThreadActivitySource
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
    "CHART_ACTIVITY_TYPE",
    "MAX_LABELS",
    "MAX_MAGNITUDE",
    "MAX_POINTS",
    "COMPACTION_ACTIVITY_TYPE",
    "ChartKind",
    "ChartSeries",
    "ChartSpec",
    "chart_activity",
    "chart_points_delta",
    "CompactionObserver",
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
    "FixedWindowThrottle",
    "LoggingAuditLogger",
    "ModelAttachmentStore",
    "ModelConversationStore",
    "NullAttachmentStore",
    "NullAuditLogger",
    "NullConversationStore",
    "NullTranscriptionBackend",
    "OpenedAttachment",
    "RunContextConfig",
    "SkillRegistry",
    "SkillSpec",
    "ThreadActivity",
    "ThreadActivitySource",
    "ThreadsView",
    "Throttle",
    "ToolBinding",
    "ToolFailureConfig",
    "ToolFailurePolicy",
    "ToolGuard",
    "ToolGuardConfig",
    "ToolCategory",
    "ScopedConversationStore",
    "ScopedStepStore",
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
