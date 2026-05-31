from __future__ import annotations

from django.test import override_settings

from django_ag_ui.conf import get_settings


def test_defaults_when_unconfigured() -> None:
    s = get_settings()
    assert s.model is None
    assert s.auto_confirm is False
    assert s.audit_logger is None
    assert s.system_prompt is None


@override_settings(
    DJANGO_AG_UI={
        "MODEL": "anthropic:claude-sonnet-4.6",
        "AUTO_CONFIRM": True,
        "AUDIT_LOGGER": "django_ag_ui.policy.audit.null_audit_logger.NullAuditLogger",
        "SYSTEM_PROMPT": "Be terse.",
    },
)
def test_reads_from_settings_dict() -> None:
    s = get_settings()
    assert s.model == "anthropic:claude-sonnet-4.6"
    assert s.auto_confirm is True
    assert s.audit_logger.endswith("NullAuditLogger")
    assert s.system_prompt == "Be terse."


@override_settings(DJANGO_AG_UI=None)
def test_none_setting_is_treated_as_empty() -> None:
    assert get_settings().auto_confirm is False
