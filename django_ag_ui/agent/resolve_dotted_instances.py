from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.utils.module_loading import import_string


def resolve_dotted_instances(paths: Sequence[str]) -> list[Any]:
    """Resolve dotted paths into instances, for ``TOOLSETS`` / ``CAPABILITIES``.

    Each path resolves to either an instance (used as-is) or a zero-arg
    callable / class returning one (invoked). The results are passed to
    :func:`~django_ag_ui.agent.agent_factory.build_agent` to compose external
    Pydantic-AI toolsets and capabilities alongside the registry tools.
    """
    instances: list[Any] = []
    for path in paths:
        obj = import_string(path)
        instances.append(obj() if callable(obj) else obj)
    return instances


__all__ = ["resolve_dotted_instances"]
