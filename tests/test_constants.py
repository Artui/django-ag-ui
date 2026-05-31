from __future__ import annotations

from django_ag_ui.constants import (
    X_CATEGORY_KEY,
    X_DESTRUCTIVE_KEY,
    ToolCategory,
)


def test_tool_category_values() -> None:
    assert ToolCategory.SHELL.value == "shell"
    assert ToolCategory.UI_WRITE.value == "ui_write"
    # Every member round-trips through its string value.
    for member in ToolCategory:
        assert ToolCategory(member.value) is member


def test_extension_keys() -> None:
    assert X_DESTRUCTIVE_KEY == "x-destructive"
    assert X_CATEGORY_KEY == "x-category"
