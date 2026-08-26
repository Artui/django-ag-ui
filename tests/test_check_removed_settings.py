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


def test_allow_anonymous_is_rejected_by_name() -> None:
    """The key that reads like a switch and is a store constructor argument.

    Nothing in this package ever read it, so a project that set it got the
    ``False`` default and no indication otherwise — the failure is silent, and
    until now the only explanation lived in a warning box in the docs.
    """
    with (
        override_settings(DJANGO_AG_UI={"ALLOW_ANONYMOUS": True}),
        pytest.raises(ImproperlyConfigured, match="allow_anonymous=True"),
    ):
        AGUIServer(ToolRegistry(), model=TestModel())


def test_an_unknown_key_is_rejected_rather_than_ignored() -> None:
    """A typo is the same silent failure as a removed key, one letter away.

    Naming the removed keys one at a time only ever covered the mistakes already
    made. Rejecting everything the package does not read is what makes the list
    exhaustive.
    """
    with (
        override_settings(DJANGO_AG_UI={"TRANSCRIPTION_MAX_BYTE": 10}),
        pytest.raises(ImproperlyConfigured) as excinfo,
    ):
        AGUIServer(ToolRegistry(), model=TestModel())
    assert "TRANSCRIPTION_MAX_BYTE" in str(excinfo.value)
    assert "check the spelling" in str(excinfo.value)


def test_every_known_key_is_accepted() -> None:
    """The guard must not reject a setting the builder actually reads.

    Exercised on the guard rather than through ``AGUIServer``, because the two
    answer different questions: this one is about which *names* pass, and a
    dict of every name at once could not also carry a valid *value* for each.
    """
    with override_settings(DJANGO_AG_UI=dict.fromkeys(_KNOWN_SETTINGS, None)):
        check_removed_settings()  # must not raise


def test_the_known_key_list_matches_what_the_builder_reads() -> None:
    """The drift guard, read off the builder's own source.

    ``_KNOWN_SETTINGS`` is what makes "anything else is rejected" safe, so a key
    added to ``build_ag_ui_config`` and forgotten here would turn a correct
    settings dict into a startup failure. Checked statically rather than by
    calling the builder, because a settings read leaves no trace at runtime.

    Nested keys (``TOOL_GUARD["ENABLED"]`` and friends) are deliberately not
    picked up: they are read with ``dict.get`` off an already-fetched value, not
    with the settings primitive, and they are not top-level ``DJANGO_AG_UI``
    keys.
    """
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
