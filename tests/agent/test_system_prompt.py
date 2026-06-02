from __future__ import annotations

from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_is_nonempty_text() -> None:
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert "tool" in DEFAULT_SYSTEM_PROMPT.lower()


def test_default_system_prompt_steers_toward_client_confirmation() -> None:
    # The model should call destructive tools and let the UI gate them, rather
    # than asking for confirmation in prose (the old behaviour).
    assert "confirmation" in DEFAULT_SYSTEM_PROMPT.lower()
