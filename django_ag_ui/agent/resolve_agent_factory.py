from __future__ import annotations

from django.utils.module_loading import import_string

from django_ag_ui.agent.types.agent_factory_fn import AgentFactoryFn


def resolve_agent_factory(path: str | None) -> AgentFactoryFn | None:
    """Resolve the ``AGENT_FACTORY`` dotted path to a callable, or ``None``.

    ``None`` (the default) means use the built-in
    :func:`~django_ag_ui.agent.agent_factory.build_agent`.
    """
    if path is None:
        return None
    return import_string(path)


__all__ = ["resolve_agent_factory"]
