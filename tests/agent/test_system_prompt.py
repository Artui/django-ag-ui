from __future__ import annotations

from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_is_nonempty_text() -> None:
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert "tool" in DEFAULT_SYSTEM_PROMPT.lower()


def test_default_system_prompt_still_speaks_about_confirmation() -> None:
    # The model must be told who confirms what, or it asks in prose for every approved action.
    assert "confirm" in DEFAULT_SYSTEM_PROMPT.lower()


def test_default_system_prompt_promises_no_gate_the_deployment_may_not_have() -> None:
    """The prompt has to be true on stock settings."""
    lower = DEFAULT_SYSTEM_PROMPT.lower()
    assert "shows the user an explicit confirmation before it runs" not in lower
    assert "do not ask for confirmation in text" not in lower


def test_default_system_prompt_keeps_a_check_for_unrequested_destructive_work() -> None:
    # What stands in for the gate the deployment may not have enabled.
    lower = DEFAULT_SYSTEM_PROMPT.lower()
    assert "irreversible" in lower
    assert "wait for" in lower


def test_default_system_prompt_steers_lookup_navigation_and_closing() -> None:
    # Raise the floor on the common failures: stopping after a lookup, and ending with no message.
    lower = DEFAULT_SYSTEM_PROMPT.lower()
    assert "navigate" in lower
    assert "empty turn" in lower
