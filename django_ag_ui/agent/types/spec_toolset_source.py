from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SpecToolsetSource(Protocol):
    """An already-built spec toolset handed to ``AGUIServer(service_specs=...)``."""

    @property
    def specs(self) -> Mapping[str, Any]: ...


__all__ = ["SpecToolsetSource"]
