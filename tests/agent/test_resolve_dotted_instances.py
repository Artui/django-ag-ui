from __future__ import annotations

from pydantic_ai.toolsets import FunctionToolset

from django_ag_ui.agent.resolve_dotted_instances import resolve_dotted_instances


def test_empty_paths_resolve_to_empty_list() -> None:
    assert resolve_dotted_instances(()) == []


def test_instance_path_is_used_as_is() -> None:
    from tests.agent import factories

    resolved = resolve_dotted_instances(["tests.agent.factories.a_toolset"])
    assert resolved == [factories.a_toolset]


def test_callable_path_is_invoked() -> None:
    resolved = resolve_dotted_instances(["tests.agent.factories.make_toolset"])
    assert len(resolved) == 1
    assert isinstance(resolved[0], FunctionToolset)
