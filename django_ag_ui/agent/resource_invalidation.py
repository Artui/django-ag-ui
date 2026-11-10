from __future__ import annotations

from ag_ui.core import CustomEvent

from django_ag_ui.agent.event_timestamp import event_timestamp

INVALIDATE_EVENT_NAME = "ag_ui.invalidate"
"""``name`` the client matches on to route an invalidation to the host page.

A **convention inside an extension point the protocol already provides**, not a
protocol extension: AG-UI defines the envelope and leaves ``name`` an open
string. A client that does not know this name ignores the event, which is the
graceful outcome and the whole reason the field is open.

A ``CUSTOM`` event rather than an ``ACTIVITY_SNAPSHOT``, which is the opposite
of the choice ``chart_activity`` makes -- and the difference is **lifetime**, not
taste. ``@ag-ui/client`` materialises an activity into a ``role: "activity"``
message; the transport persists the message list wholesale, and the client's
replay path re-fires it on every thread restore. That is right for a chart, which
is content and should come back. An invalidation is an *imperative*: it has no
place in the conversation and no meaning once acted on, so replaying it on every
thread load would be a refetch storm.

**``ACTIVITY_SNAPSHOT`` is for content; ``CUSTOM`` is for an imperative.**

``STATE_SNAPSHOT`` is out for a third reason: shared state round-trips into the
next ``RunAgentInput``, so an invalidation placed there would be echoed back to
the model as though it were something the model had said.
"""


def resource_invalidation(*keys: str, reason: str | None = None) -> CustomEvent:
    """An event naming the resources a write has just moved."""
    return CustomEvent(
        name=INVALIDATE_EVENT_NAME,
        value={"keys": list(keys), "reason": reason},
        timestamp=event_timestamp(),
    )


__all__ = ["INVALIDATE_EVENT_NAME", "resource_invalidation"]
