from __future__ import annotations

from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_is_nonempty_text() -> None:
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert "tool" in DEFAULT_SYSTEM_PROMPT.lower()
