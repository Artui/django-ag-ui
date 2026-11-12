from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest


@runtime_checkable
class Throttle(Protocol):
    """Rate limiter for the agent run endpoint, evaluated after authentication.

    A single ``consume`` call is the gate **and** the bookkeeping update — there
    is deliberately no separate "check, then commit", a shape that races under
    exactly the concurrency an agent endpoint sees. Implementations decrement
    quotas atomically in shared storage and return the suggested ``Retry-After``
    in seconds, or ``None`` to allow the run. ``0`` is allowed (denied, but the
    window resets immediately); most implementations return a positive integer.

    Running after authentication is what lets a limiter key on the acting user
    rather than only on an IP: ``request.user`` is resolved by the time this is
    called, including through a ``get_user`` hook.

    ``consume`` is **synchronous** and is run off the event loop, so it may
    safely touch the Django cache or the ORM. An ``async def consume`` is refused
    at construction rather than awaited, because a coroutine returned into an
    ``is not None`` check would throttle every request on a bogus
    ``Retry-After``.

    State that crosses requests must live in shared storage (the Django cache,
    Redis), never on the instance — an endpoint is constructed once per process,
    so instance counters would enforce a per-worker limit while reading like a
    global one.

    Mirrors ``djangorestframework-mcp-server``'s ``MCPRateLimit``, so a project
    protecting both transports writes one kind of limiter.
    """

    def consume(self, request: HttpRequest) -> int | None: ...


__all__ = ["Throttle"]
