from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest

_KEY_PREFIX = "django-ag-ui:throttle"


def _default_key(request: HttpRequest) -> str:
    """Per-user when authenticated, per-IP otherwise."""
    user: Any = getattr(request, "user", None)
    user_id: Any = getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


class FixedWindowThrottle:
    """A fixed-window run limiter backed by ``django.core.cache``."""

    def __init__(
        self,
        *,
        max_runs: int,
        per_seconds: int,
        namespace: str = "default",
        key: Callable[[HttpRequest], str] | None = None,
    ) -> None:
        if max_runs <= 0:
            raise ValueError("max_runs must be positive")
        if per_seconds <= 0:
            raise ValueError("per_seconds must be positive")
        self._max = max_runs
        self._window = per_seconds
        self._namespace = namespace
        self._key_fn: Callable[[HttpRequest], str] = key or _default_key

    def consume(self, request: HttpRequest) -> int | None:
        now = int(time.time())
        bucket = now // self._window
        cache_key = f"{_KEY_PREFIX}:{self._namespace}:{self._key_fn(request)}:{bucket}"
        # ``add`` initialises the counter atomically; ``incr`` bumps it.
        if cache.add(cache_key, 1, timeout=self._window):
            return None
        count = self._increment(cache_key)
        if count <= self._max:
            return None
        return max((bucket + 1) * self._window - now, 1)

    def _increment(self, cache_key: str) -> int:
        """Bump the window's counter, tolerating an eviction between the two calls."""
        try:
            return cache.incr(cache_key)
        except ValueError:
            cache.add(cache_key, 1, timeout=self._window)
            return 1


__all__ = ["FixedWindowThrottle"]
