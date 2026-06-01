from __future__ import annotations

from django.test import override_settings

from django_ag_ui.conf import get_settings


def test_defaults_when_unconfigured() -> None:
    s = get_settings()
    assert s.model is None
    assert s.auto_confirm is False
    assert s.audit_logger is None
    assert s.system_prompt is None
    assert s.model_settings is None
    assert s.retries is None
    assert s.agent_factory is None
    assert s.toolsets == ()
    assert s.capabilities == ()
    assert s.conversation_store is None
    assert s.drf_mcp_server is None


@override_settings(
    DJANGO_AG_UI={
        "MODEL": "anthropic:claude-sonnet-4.6",
        "AUTO_CONFIRM": True,
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
        "DRF_MCP_SERVER": "myapp.mcp.server",
    },
)
def test_reads_from_settings_dict() -> None:
    s = get_settings()
    assert s.model == "anthropic:claude-sonnet-4.6"
    assert s.auto_confirm is True
    assert s.audit_logger.endswith("NullAuditLogger")
    assert s.system_prompt == "Be terse."
    assert s.model_settings == {"temperature": 0.2, "max_tokens": 512}
    assert s.retries == 3
    assert s.agent_factory == "tests.agent.factories.build_test_agent"
    assert s.toolsets == ("tests.agent.factories.a_toolset",)
    assert s.capabilities == ("tests.agent.factories.make_toolset",)
    assert s.conversation_store is not None
    assert s.conversation_store.endswith("NullConversationStore")
    assert s.drf_mcp_server == "myapp.mcp.server"


@override_settings(DJANGO_AG_UI=None)
def test_none_setting_is_treated_as_empty() -> None:
    assert get_settings().auto_confirm is False
