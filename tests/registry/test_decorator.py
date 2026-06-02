from __future__ import annotations

from django_ag_ui.constants import ToolCategory
from django_ag_ui.registry.decorator import tool
from django_ag_ui.registry.tool_registry import ToolRegistry


def test_decorator_uses_function_name_and_docstring() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def echo(value: str) -> str:
        """Echo the value back.

        Second paragraph that should be dropped.
        """
        return value

    binding = reg.get("echo")
    assert binding.spec.description == "Echo the value back."
    assert binding.spec.destructive is False
    assert binding.spec.category is ToolCategory.OTHER
    assert reg.call("echo", {"value": "hi"}) == "hi"


def test_decorator_overrides_take_precedence() -> None:
    reg = ToolRegistry()

    @tool(
        reg,
        name="custom",
        description="overridden",
        destructive=True,
        category=ToolCategory.UI_WRITE,
        confirm="Run the custom action?",
        summary="Custom action",
    )
    def fn() -> int:
        return 1

    binding = reg.get("custom")
    assert binding.spec.description == "overridden"
    assert binding.spec.destructive is True
    assert binding.spec.category is ToolCategory.UI_WRITE
    assert binding.spec.confirm == "Run the custom action?"
    assert binding.spec.summary == "Custom action"
    assert binding.input_schema["x-destructive"] is True
    assert binding.input_schema["x-confirm"] == "Run the custom action?"
    assert binding.input_schema["x-summary"] == "Custom action"


def test_decorator_handles_missing_docstring() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def bare() -> None:
        return None

    assert reg.get("bare").spec.description == ""


def test_decorator_single_line_docstring_no_blank_line() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def oneliner() -> None:
        "Single line, no blank line — loop exhausts without a break."

    assert (
        reg.get("oneliner").spec.description
        == "Single line, no blank line — loop exhausts without a break."
    )


def test_decorator_strips_to_first_paragraph() -> None:
    reg = ToolRegistry()

    @tool(reg)
    def multi() -> None:
        """First line of summary
        continues onto a second line.

        Second paragraph.
        """

    assert (
        reg.get("multi").spec.description == "First line of summary continues onto a second line."
    )
