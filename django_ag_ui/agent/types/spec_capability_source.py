from __future__ import annotations

from typing import Protocol, runtime_checkable

from django_ag_ui.agent.types.spec_toolset_source import SpecToolsetSource


@runtime_checkable
class SpecCapabilitySource(Protocol):
    """An already-built spec capability handed to ``AGUIServer(service_specs=...)``."""

    def get_toolset(self) -> SpecToolsetSource: ...


__all__ = ["SpecCapabilitySource"]
