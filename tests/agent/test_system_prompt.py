from __future__ import annotations

from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_is_nonempty_text() -> None:
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert "tool" in DEFAULT_SYSTEM_PROMPT.lower()


def test_default_system_prompt_steers_toward_client_confirmation() -> None:
    # The model should call destructive tools and let the UI gate them, rather
    # than asking for confirmation in prose (the old behaviour).
    assert "confirmation" in DEFAULT_SYSTEM_PROMPT.lower()


def test_default_system_prompt_steers_lookup_navigation_and_closing() -> None:
    # Raise the floor on the common failure modes: stopping after a lookup
    # instead of acting, and ending a run with no closing message.
    lower = DEFAULT_SYSTEM_PROMPT.lower()
    assert "navigate" in lower
    assert "empty turn" in lower
