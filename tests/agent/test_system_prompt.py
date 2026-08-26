from __future__ import annotations

from django_ag_ui.agent.system_prompt import DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_is_nonempty_text() -> None:
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert "tool" in DEFAULT_SYSTEM_PROMPT.lower()


def test_default_system_prompt_still_speaks_about_confirmation() -> None:
    # The model has to be told *something* about who confirms what, or it falls
    # back to asking in prose for every action the user already asked for.
    assert "confirm" in DEFAULT_SYSTEM_PROMPT.lower()


def test_default_system_prompt_promises_no_gate_the_deployment_may_not_have() -> None:
    """The prompt has to be true on stock settings.

    The only server-side gate is an opt-in one, and it is off by default, so a
    prompt that states the interface always confirms a destructive action
    before it runs is false in the shipped configuration — and it is false in
    the direction that removes the last safeguard, because it also tells the
    model not to ask in text.
    """
    lower = DEFAULT_SYSTEM_PROMPT.lower()
    assert "shows the user an explicit confirmation before it runs" not in lower
    assert "do not ask for confirmation in text" not in lower


def test_default_system_prompt_keeps_a_check_for_unrequested_destructive_work() -> None:
    # What stands in for the gate the deployment may not have enabled.
    lower = DEFAULT_SYSTEM_PROMPT.lower()
    assert "irreversible" in lower
    assert "wait for" in lower


def test_default_system_prompt_steers_lookup_navigation_and_closing() -> None:
    # Raise the floor on the common failure modes: stopping after a lookup
    # instead of acting, and ending a run with no closing message.
    lower = DEFAULT_SYSTEM_PROMPT.lower()
    assert "navigate" in lower
    assert "empty turn" in lower
