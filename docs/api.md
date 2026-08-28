# API reference

Autodoc of the public surface re-exported from `django_ag_ui`. Everything below
is importable directly, e.g. `from django_ag_ui import ToolRegistry`.

## Registry

::: django_ag_ui.ToolRegistry

::: django_ag_ui.tool

::: django_ag_ui.build_input_schema

::: django_ag_ui.ToolSpec

::: django_ag_ui.ToolBinding

::: django_ag_ui.ToolCategory

::: django_ag_ui.X_DESTRUCTIVE_KEY

::: django_ag_ui.X_CATEGORY_KEY

::: django_ag_ui.X_CONFIRM_KEY

::: django_ag_ui.X_SUMMARY_KEY

## Skills

::: django_ag_ui.SkillRegistry

::: django_ag_ui.SkillSpec

::: django_ag_ui.skills.skills_view.SkillsView

## Agent and view

::: django_ag_ui.AGUIServer

::: django_ag_ui.DjangoAGUIView

::: django_ag_ui.AgentSession

::: django_ag_ui.ToolsView

::: django_ag_ui.build_agent

::: django_ag_ui.AgentConfig

::: django_ag_ui.AgentFactoryFn

::: django_ag_ui.DEFAULT_SYSTEM_PROMPT

## Configuration

::: django_ag_ui.AGUIConfig

::: django_ag_ui.RunContextConfig

::: django_ag_ui.ContextDelivery

::: django_ag_ui.build_ag_ui_config

## Policy and audit

::: django_ag_ui.AuditLogger

::: django_ag_ui.AuditCapability

::: django_ag_ui.AuditEvent

::: django_ag_ui.NullAuditLogger

::: django_ag_ui.LoggingAuditLogger


## Rate limiting

The seam both spending routes take — the agent endpoint via `throttle=` and
`transcribe/` via `transcribe_throttle=`. See
[`throttle=`](configuration.md#throttle) for the contract and its ordering.

::: django_ag_ui.Throttle

::: django_ag_ui.FixedWindowThrottle

## Conversation persistence

::: django_ag_ui.ConversationStore

::: django_ag_ui.Conversation

::: django_ag_ui.ConversationMeta

::: django_ag_ui.NullConversationStore

::: django_ag_ui.ScopedConversationStore

::: django_ag_ui.DjangoSessionConversationStore

::: django_ag_ui.ModelConversationStore


::: django_ag_ui.ThreadsView

::: django_ag_ui.ThreadActivitySource

::: django_ag_ui.ThreadActivity

### Reference store (opt-in)

The `django_pydantic_agent.contrib.store` app ships a ready-to-use durable store. Add it
to `INSTALLED_APPS`, run `migrate`, then set
`conversation_store=` to
`django_pydantic_agent.contrib.store.default_conversation_store.DefaultConversationStore`.
Projects that don't opt in get no model and no migration.

::: django_pydantic_agent.contrib.store.default_conversation_store.DefaultConversationStore

::: django_pydantic_agent.contrib.store.models.StoredConversation

## Step persistence

The durable run / event / snapshot ledger behind `resume/`, `fork/` and `runs/`.
The store itself is a `request -> StepStore` factory from
`pydantic-ai-harness` (the reference one is
`django_pydantic_agent.contrib.store.default_step_store.DefaultStepStore`); this
is the wrapper that keeps two endpoints' ledgers apart, the way
[`ScopedConversationStore`][django_ag_ui.ScopedConversationStore] does for
thread history.

::: django_ag_ui.ScopedStepStore

## File uploads

::: django_ag_ui.AttachmentStore

::: django_ag_ui.AttachmentRef

::: django_ag_ui.OpenedAttachment

::: django_ag_ui.NullAttachmentStore

::: django_ag_ui.ModelAttachmentStore


::: django_ag_ui.AttachmentsView

### Reference attachment store (opt-in)

The same `django_pydantic_agent.contrib.store` app ships a ready-to-use durable file
store. With the app installed and migrated, set
`attachment_store=` to
`django_pydantic_agent.contrib.store.default_attachment_store.DefaultAttachmentStore`. The
bytes go to Django `Storage` (S3/GCS via `STORAGES` / `DEFAULT_FILE_STORAGE`);
projects that don't opt in get no model and no migration.

::: django_pydantic_agent.contrib.store.default_attachment_store.DefaultAttachmentStore

::: django_pydantic_agent.contrib.store.models.StoredAttachment

## Voice input

::: django_ag_ui.TranscriptionBackend

::: django_ag_ui.NullTranscriptionBackend


::: django_ag_ui.TranscribeView

### Reference transcription backend (opt-in)

A ready-to-use backend over any OpenAI-compatible `/audio/transcriptions`
endpoint. Install the `[openai]` extra and set
`transcription_backend=` to
`django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend`;
subclass it to change the model or point at another OpenAI-compatible server.

::: django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend

## Charts

::: django_ag_ui.ChartSpec

::: django_ag_ui.ChartSeries

::: django_ag_ui.ChartKind

::: django_ag_ui.chart_activity

::: django_ag_ui.chart_points_delta

::: django_ag_ui.CHART_ACTIVITY_TYPE

## Follow-up suggestions

::: django_ag_ui.suggestions_activity

::: django_ag_ui.SUGGESTIONS_ACTIVITY_TYPE

::: django_ag_ui.MAX_SUGGESTIONS

::: django_ag_ui.MAX_SUGGESTION_CHARS

## Host data invalidation

::: django_ag_ui.publish_invalidation

::: django_ag_ui.resource_invalidation

::: django_ag_ui.INVALIDATE_EVENT_NAME

## Internal helpers

These are not part of the public re-export surface but are referenced from the
guides.


::: django_pydantic_agent.agent.build_model.build_model

::: django_pydantic_agent.agent.build_tool_catalog.build_tool_catalog

::: django_pydantic_agent.integrations.drf_mcp.DRFMCPToolset

::: django_pydantic_agent.agent.attachment_toolset.build_attachment_toolset
</content>
