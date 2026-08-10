from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest

_KEY_PREFIX = "django-ag-ui:throttle"


def _default_key(request: HttpRequest) -> str:
    """Per-user when authenticated, per-IP otherwise.

    The acting user is resolved by the time a throttle runs, so an
    authenticated caller is limited as themselves rather than sharing a bucket
    with everyone behind the same proxy. Anonymous callers fall back to the
    remote address, which is the only identity available.
    """
    user: Any = getattr(request, "user", None)
    user_id: Any = getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


class FixedWindowThrottle:
    """A fixed-window run limiter backed by ``django.core.cache``.

    The window is bucketed by absolute time: every integer multiple of
    ``per_seconds`` since the epoch starts a fresh counter. Simpler than a
    sliding window and sufficient for the thing this protects against — one
    client starting runs faster than a human could read them, on an endpoint
    where each run costs a model call.

    ``namespace`` separates counters so two throttles on one endpoint (a burst
    limit and a steady-state limit) do not share a bucket. ``key`` chooses the
    bucket dimension; the default is per-user, falling back to per-IP.

    The cache **must** be a shared backend in a multi-process deployment.
    Django's ``locmem`` cache is fine in tests but enforces a per-worker limit
    that reads like a global one.

    Mirrors ``djangorestframework-mcp-server``'s ``FixedWindowRateLimit``,
    including the ``add``-then-``incr`` primitive, so the two transports behave
    the same way under the same configuration.
    """

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
        # ``add`` initialises the counter to 1 atomically when absent and
        # ``incr`` bumps it; together they are the fixed-window primitive that
        # works on Memcached, Redis and Django's locmem cache alike. A
        # read-then-write would lose counts to the concurrency this exists for.
        if cache.add(cache_key, 1, timeout=self._window):
            return None
        count = self._increment(cache_key)
        if count <= self._max:
            return None
        # The window resets at the next bucket boundary; never report 0, which
        # a client reads as "retry immediately".
        return max((bucket + 1) * self._window - now, 1)

    def _increment(self, cache_key: str) -> int:
        """Bump the window's counter, tolerating an eviction between the two calls.

        ``incr`` raises ``ValueError`` when the key is gone — a cache eviction,
        or the window expiring in the microseconds since ``add`` reported it
        present. Re-seeding is the honest response: the window this request
        belongs to has no surviving record, so it is the first call in it.
        """
        try:
            return cache.incr(cache_key)
        except ValueError:
            cache.add(cache_key, 1, timeout=self._window)
            return 1


__all__ = ["FixedWindowThrottle"]
