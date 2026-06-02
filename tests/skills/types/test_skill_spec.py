from __future__ import annotations

import pytest

from django_ag_ui.skills.types.skill_spec import SkillSpec


def test_defaults_and_frozen() -> None:
    spec = SkillSpec(name="x", title="X", prompt="do x")
    assert spec.description is None
    assert spec.send_immediately is False
    assert spec.chip is False
    with pytest.raises(AttributeError):
        spec.name = "y"  # type: ignore[misc]
