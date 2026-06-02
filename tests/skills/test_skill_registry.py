from __future__ import annotations

import pytest

from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.types.skill_spec import SkillSpec


def test_add_register_iter_len() -> None:
    reg = SkillRegistry()
    reg.add("summarize", "Summarize", "Summarize this.", chip=True)
    reg.register(SkillSpec(name="draft", title="Draft", prompt="Draft it."))
    assert len(reg) == 2
    assert [s.name for s in reg] == ["summarize", "draft"]


def test_duplicate_name_raises() -> None:
    reg = SkillRegistry()
    reg.add("x", "X", "p")
    with pytest.raises(ValueError, match="already registered"):
        reg.add("x", "X2", "p2")


def test_payload_uses_camel_case_and_omits_defaults() -> None:
    reg = SkillRegistry()
    reg.add("plain", "Plain", "just a prompt")
    reg.add(
        "rich",
        "Rich",
        "Find {q}.",
        description="search records",
        send_immediately=True,
        chip=True,
    )
    assert reg.payload() == [
        {"name": "plain", "title": "Plain", "prompt": "just a prompt"},
        {
            "name": "rich",
            "title": "Rich",
            "prompt": "Find {q}.",
            "description": "search records",
            "sendImmediately": True,
            "chip": True,
        },
    ]
