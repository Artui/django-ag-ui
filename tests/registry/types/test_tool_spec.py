from __future__ import annotations

import pytest

from django_ag_ui.constants import ToolCategory
from django_ag_ui.registry.types.tool_binding import ToolBinding
from django_ag_ui.registry.types.tool_spec import ToolSpec


def test_tool_spec_defaults_and_frozen() -> None:
    spec = ToolSpec(name="x", fn=lambda: None, description="d")
    assert spec.destructive is False
    assert spec.category is ToolCategory.OTHER
    with pytest.raises(AttributeError):
        spec.name = "y"  # type: ignore[misc]


def test_tool_binding_carries_schema() -> None:
    binding = ToolBinding(
        spec=ToolSpec(name="x", fn=lambda: None, description="d"),
        input_schema={"type": "object"},
    )
    assert binding.input_schema == {"type": "object"}
