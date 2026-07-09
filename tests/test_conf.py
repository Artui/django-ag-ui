from __future__ import annotations

from django.test import override_settings

from django_ag_ui.conf import get_settings


def test_defaults_when_unconfigured() -> None:
    s = get_settings()
    assert s.model is None
    assert s.api_key is None
    assert s.provider is None
    assert s.audit_logger is None
    assert s.system_prompt is None
    assert s.model_settings is None
    assert s.retries is None
    assert s.agent_factory is None
    assert s.toolsets == ()
    assert s.capabilities == ()
    assert s.conversation_store is None
    assert s.attachment_store is None
    assert s.attachment_max_bytes == 10 * 1024 * 1024
    assert s.attachment_allowed_types == ()
    assert s.forward_reasoning is True
    assert s.transcription_backend is None
    assert s.transcription_max_bytes == 25 * 1024 * 1024
    assert s.transcription_allowed_types == ()
    assert s.drf_mcp_server is None
    assert s.service_specs is None


@override_settings(
    DJANGO_AG_UI={
        "MODEL": "anthropic:claude-sonnet-4.6",
        "API_KEY": "sk-test",
        "PROVIDER": "myapp.providers.custom",
        "AUDIT_LOGGER": "django_ag_ui.policy.audit.null_audit_logger.NullAuditLogger",
        "SYSTEM_PROMPT": "Be terse.",
        "MODEL_SETTINGS": {"temperature": 0.2, "max_tokens": 512},
        "RETRIES": 3,
        "AGENT_FACTORY": "tests.agent.factories.build_test_agent",
        "TOOLSETS": ["tests.agent.factories.a_toolset"],
        "CAPABILITIES": ["tests.agent.factories.make_toolset"],
        "CONVERSATION_STORE": (
            "django_ag_ui.persistence.null_conversation_store.NullConversationStore"
        ),
        "ATTACHMENT_STORE": ("django_ag_ui.persistence.null_attachment_store.NullAttachmentStore"),
        "ATTACHMENT_MAX_BYTES": 2048,
        "ATTACHMENT_ALLOWED_TYPES": ["text/plain", "image/png"],
        "FORWARD_REASONING": False,
        "TRANSCRIPTION_BACKEND": (
            "django_ag_ui.persistence.null_transcription_backend.NullTranscriptionBackend"
        ),
        "TRANSCRIPTION_MAX_BYTES": 4096,
        "TRANSCRIPTION_ALLOWED_TYPES": ["audio/webm", "audio/mp4"],
        "DRF_MCP_SERVER": "myapp.mcp.server",
        "SERVICE_SPECS": "myapp.specs.SPECS",
    },
)
def test_reads_from_settings_dict() -> None:
    s = get_settings()
    assert s.model == "anthropic:claude-sonnet-4.6"
    assert s.api_key == "sk-test"
    assert s.provider == "myapp.providers.custom"
    assert s.audit_logger.endswith("NullAuditLogger")
    assert s.system_prompt == "Be terse."
    assert s.model_settings == {"temperature": 0.2, "max_tokens": 512}
    assert s.retries == 3
    assert s.agent_factory == "tests.agent.factories.build_test_agent"
    assert s.toolsets == ("tests.agent.factories.a_toolset",)
    assert s.capabilities == ("tests.agent.factories.make_toolset",)
    assert s.conversation_store is not None
    assert s.conversation_store.endswith("NullConversationStore")
    assert s.attachment_store is not None
    assert s.attachment_store.endswith("NullAttachmentStore")
    assert s.attachment_max_bytes == 2048
    assert s.attachment_allowed_types == ("text/plain", "image/png")
    assert s.forward_reasoning is False
    assert s.transcription_backend is not None
    assert s.transcription_backend.endswith("NullTranscriptionBackend")
    assert s.transcription_max_bytes == 4096
    assert s.transcription_allowed_types == ("audio/webm", "audio/mp4")
    assert s.drf_mcp_server == "myapp.mcp.server"
    assert s.service_specs == "myapp.specs.SPECS"


@override_settings(DJANGO_AG_UI=None)
def test_none_setting_is_treated_as_empty() -> None:
    assert get_settings().model is None
