from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest


@runtime_checkable
class Throttle(Protocol):
    """Rate limiter for the agent run endpoint, evaluated after authentication."""

    def consume(self, request: HttpRequest) -> int | None: ...


__all__ = ["Throttle"]
