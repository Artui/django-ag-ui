"""Django ↔ Pydantic-AI ↔ AG-UI integration."""

from django_ag_ui.constants import (
    X_CATEGORY_KEY,
    X_DESTRUCTIVE_KEY,
    ToolCategory,
)
from django_ag_ui.registry.build_input_schema import build_input_schema
from django_ag_ui.registry.decorator import tool
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.registry.types.tool_binding import ToolBinding
from django_ag_ui.registry.types.tool_spec import ToolSpec
from django_ag_ui.version import __version__

__all__ = [
    "ToolBinding",
    "ToolCategory",
    "ToolRegistry",
    "ToolSpec",
    "X_CATEGORY_KEY",
    "X_DESTRUCTIVE_KEY",
    "__version__",
    "build_input_schema",
    "tool",
]
