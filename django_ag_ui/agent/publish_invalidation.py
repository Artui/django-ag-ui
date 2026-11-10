from __future__ import annotations

from contextvars import ContextVar

from ag_ui.core import CustomEvent

from django_ag_ui.agent.resource_invalidation import resource_invalidation

# The per-run sink ``publish_invalidation`` appends to, drained by
# ``inject_invalidation_events`` while it forwards the AG-UI stream.
#
# A ``ContextVar`` rather than module state, for the reason ``COMPACTION_SINK``
# gives: an instance-level list would interleave concurrent runs into each
# other's transcripts. Context variables are per-task, and a call made outside a
# run -- from a management command, a worker, a test -- finds no sink and is a
# no-op rather than an error.
INVALIDATION_SINK: ContextVar[list[CustomEvent] | None] = ContextVar(
    "django_ag_ui_invalidation_sink", default=None
)


def publish_invalidation(*keys: str, reason: str | None = None) -> bool:
    """Queue an invalidation for the run currently streaming, without returning it."""
    sink = INVALIDATION_SINK.get()
    if sink is None:
        return False
    sink.append(resource_invalidation(*keys, reason=reason))
    return True


__all__ = ["INVALIDATION_SINK", "publish_invalidation"]
