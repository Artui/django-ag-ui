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

::: django_ag_ui.DjangoAGUIView

::: django_ag_ui.ToolsView

::: django_ag_ui.get_urls

::: django_ag_ui.build_agent

::: django_ag_ui.AgentConfig

::: django_ag_ui.AgentFactoryFn

::: django_ag_ui.DEFAULT_SYSTEM_PROMPT

## Configuration

::: django_ag_ui.AppSettings

::: django_ag_ui.get_settings

## Policy and audit

::: django_ag_ui.needs_confirmation

::: django_ag_ui.AuditLogger

::: django_ag_ui.AuditEvent

::: django_ag_ui.NullAuditLogger

::: django_ag_ui.LoggingAuditLogger

::: django_ag_ui.resolve_audit_logger

## Conversation persistence

::: django_ag_ui.ConversationStore

::: django_ag_ui.Conversation

::: django_ag_ui.NullConversationStore

::: django_ag_ui.DjangoSessionConversationStore

::: django_ag_ui.ModelConversationStore

::: django_ag_ui.resolve_conversation_store

## Internal helpers

These are not part of the public re-export surface but are referenced from the
guides.

::: django_ag_ui.agent.resolve_dotted_instances.resolve_dotted_instances

::: django_ag_ui.agent.build_model.build_model

::: django_ag_ui.agent.build_tool_catalog.build_tool_catalog

::: django_ag_ui.integrations.drf_mcp.DrfMcpToolset
</content>
