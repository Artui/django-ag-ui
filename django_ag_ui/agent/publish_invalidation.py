"""``publish_invalidation`` -- queue an invalidation onto the run's own stream."""

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
    """Queue an invalidation for the run currently streaming, without returning it.

    The sibling of :func:`resource_invalidation`, and the one to reach for
    **inside a transaction**. Returning an event as tool metadata is the simpler
    route and is right when nothing is being written transactionally, but it
    cannot answer the ordering question at all::

        @agent.tool_plain
        def place_order(sku: str) -> str:
            with transaction.atomic():
                order = Order.objects.create(sku=sku)
                # Fires when the transaction commits -- not before, and not at
                # all if it rolls back.
                transaction.on_commit(
                    lambda: publish_invalidation("orders", f"orders/{order.pk}",
                                                 reason="place_order")
                )
            return f"Ordered {sku}."

    **Announcing before the commit is wrong by default, not in an edge case.**
    A ``ServiceSpec`` is ``atomic=True`` unless told otherwise, so the naive
    version tells the page to refetch data that has not committed -- and may
    never commit. The page then re-reads the *old* row and caches it as fresh,
    which is worse than not having been told.

    **Register the callback with ``transaction.on_commit``, from the thread
    that owns the transaction.** Django connections are thread-local, so code
    running on the event loop sees no transaction at all and any callback it
    registers runs immediately -- announcing early while looking correct. In this
    package's async topology that means registering from inside whatever
    ``sync_to_async(..., thread_sensitive=True)`` wrapper is already doing the
    write, which is where the ORM call is anyway.

    Returns whether a run was listening. ``False`` means there was no stream to
    queue onto -- a call from off-HTTP code -- and nothing happened.
    """
    sink = INVALIDATION_SINK.get()
    if sink is None:
        return False
    sink.append(resource_invalidation(*keys, reason=reason))
    return True


__all__ = ["INVALIDATION_SINK", "publish_invalidation"]
