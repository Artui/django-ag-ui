from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MountIdentity:
    """Who a mount is, from ``AGUIServer(id=, name=, description=)``, for the threads body."""

    id: str | None = None
    name: str | None = None
    description: str | None = None


__all__ = ["MountIdentity"]
