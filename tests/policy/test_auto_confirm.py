from __future__ import annotations

import pytest

from django_ag_ui.policy.auto_confirm import needs_confirmation
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.registry.types.tool_spec import ToolSpec


def _binding(*, destructive: bool):  # noqa: ANN202
    reg = ToolRegistry()
    return reg.register(
        ToolSpec(name="t", fn=lambda: None, description="d", destructive=destructive)
    )


@pytest.mark.parametrize(
    ("destructive", "auto_confirm", "expected"),
    [
        (True, False, True),
        (True, True, False),
        (False, False, False),
        (False, True, False),
    ],
)
def test_needs_confirmation_matrix(destructive: bool, auto_confirm: bool, expected: bool) -> None:
    binding = _binding(destructive=destructive)
    assert needs_confirmation(binding, auto_confirm=auto_confirm) is expected
