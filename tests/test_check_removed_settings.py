from __future__ import annotations

import ast
import pathlib

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from pydantic_ai.models.test import TestModel

from django_ag_ui import AGUIServer, ToolRegistry, build_ag_ui_config
from django_ag_ui.check_removed_settings import _KNOWN_SETTINGS, check_removed_settings


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
    """Ignoring a stale TOOLSETS silently would strip an agent's tools, so it raises."""
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


def test_allow_anonymous_is_rejected_by_name() -> None:
    """The key that reads like a switch and is a store constructor argument."""
    with (
        override_settings(DJANGO_AG_UI={"ALLOW_ANONYMOUS": True}),
        pytest.raises(ImproperlyConfigured, match="allow_anonymous=True"),
    ):
        AGUIServer(ToolRegistry(), model=TestModel())


def test_an_unknown_key_is_rejected_rather_than_ignored() -> None:
    """A typo is the same silent failure as a removed key, one letter away."""
    with (
        override_settings(DJANGO_AG_UI={"TRANSCRIPTION_MAX_BYTE": 10}),
        pytest.raises(ImproperlyConfigured) as excinfo,
    ):
        AGUIServer(ToolRegistry(), model=TestModel())
    assert "TRANSCRIPTION_MAX_BYTE" in str(excinfo.value)
    assert "check the spelling" in str(excinfo.value)


def test_every_known_key_is_accepted() -> None:
    """The guard must not reject a setting the builder actually reads."""
    with override_settings(DJANGO_AG_UI=dict.fromkeys(_KNOWN_SETTINGS, None)):
        check_removed_settings()


def test_the_known_key_list_matches_what_the_builder_reads() -> None:
    """The drift guard, read off the builder's own source."""
    source = pathlib.Path(build_ag_ui_config.__code__.co_filename).read_text()
    read: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"pick", "get_setting"}:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                read.add(arg.value)
                break

    assert read == set(_KNOWN_SETTINGS)
