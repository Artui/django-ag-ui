from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from pydantic_ai.models.test import TestModel

from django_ag_ui import AGUIServer, ToolRegistry


@pytest.mark.parametrize(
    "removed",
    [
        {"TOOLSETS": ["some.dotted.Path"]},
        {"CAPABILITIES": ["some.dotted.Path"]},
        {"AGENT_FACTORY": "some.dotted.path"},
        {"AUDIT_LOGGER": "some.dotted.Path"},
        {"CONVERSATION_STORE": "some.dotted.Path"},
        {"ATTACHMENT_STORE": "some.dotted.Path"},
        {"TRANSCRIPTION_BACKEND": "some.dotted.Path"},
        {"DRF_MCP_SERVER": "some.dotted.server"},
        {"SERVICE_SPECS": "some.dotted.SPECS"},
        {"PROVIDER": "some.dotted.Provider"},
    ],
)
def test_removed_collaborator_settings_raise(removed: dict[str, object]) -> None:
    """Silently ignoring a stale TOOLSETS would mean an agent quietly loses its
    tools; a stale TOOL_GUARD-adjacent key, that a project runs ungated."""
    with (
        override_settings(DJANGO_AG_UI=removed),
        pytest.raises(ImproperlyConfigured, match="removed in 0.19.0"),
    ):
        AGUIServer(ToolRegistry(), model=TestModel())


def test_the_error_names_the_replacement() -> None:
    with (
        override_settings(DJANGO_AG_UI={"TOOLSETS": ["x.y"]}),
        pytest.raises(ImproperlyConfigured, match=r"toolsets=\[YourToolset\(\)\]"),
    ):
        AGUIServer(ToolRegistry(), model=TestModel())


def test_every_removed_key_is_listed_at_once() -> None:
    """One deploy, one fix-list — not one error per round-trip."""
    with (
        override_settings(DJANGO_AG_UI={"TOOLSETS": ["x.y"], "AUDIT_LOGGER": "x.z"}),
        pytest.raises(ImproperlyConfigured) as excinfo,
    ):
        AGUIServer(ToolRegistry(), model=TestModel())
    message = str(excinfo.value)
    assert "TOOLSETS" in message
    assert "AUDIT_LOGGER" in message


def test_a_clean_settings_dict_passes() -> None:
    with override_settings(DJANGO_AG_UI={"RETRIES": 2}):
        AGUIServer(ToolRegistry(), model=TestModel())
