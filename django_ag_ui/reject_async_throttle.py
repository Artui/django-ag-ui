from __future__ import annotations

from asgiref.sync import iscoroutinefunction
from django.core.exceptions import ImproperlyConfigured

from django_ag_ui.agent.types.throttle import Throttle


def reject_async_throttle(throttle: Throttle | None, *, allows: str) -> None:
    """Refuse an ``async def consume`` at construction, not at request time."""
    if throttle is None or not iscoroutinefunction(throttle.consume):  # ty: ignore[deprecated]
        return
    raise ImproperlyConfigured(
        f"{type(throttle).__name__}.consume is declared 'async def', but the "
        "throttle hook is synchronous. django-ag-ui runs it off the event "
        "loop, so it may touch the cache or the ORM directly -- declare it "
        "'def consume(self, request)' and return the Retry-After seconds, or "
        f"None to allow the {allows}."
    )


__all__ = ["reject_async_throttle"]
