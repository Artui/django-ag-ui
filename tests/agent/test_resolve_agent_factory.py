from __future__ import annotations

from django_ag_ui.agent.resolve_agent_factory import resolve_agent_factory


def test_none_path_returns_none() -> None:
    assert resolve_agent_factory(None) is None


def test_dotted_path_resolves_to_callable() -> None:
    from tests.agent.factories import build_test_agent

    assert resolve_agent_factory("tests.agent.factories.build_test_agent") is build_test_agent
