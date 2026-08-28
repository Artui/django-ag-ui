"""``resource_invalidation`` -- an AG-UI event telling the page what moved."""

from __future__ import annotations

from ag_ui.core import CustomEvent

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
    """An event naming the resources a write has just moved.

    Emitted by the project from its own code, where it knows what it wrote.
    There is no setting that turns this on and nothing derives it for you: the
    framework has no idea which of your pages read which of your tables.

    ``keys`` are **opaque host-defined strings**. This package never interprets
    them, compares them to anything, or requires a scheme -- it carries them.
    That is the load-bearing choice: every alternative (model labels, spec names,
    URLs, MCP-style URIs) encodes one side's vocabulary into a contract both
    sides have to read, and the two sides here are a Django app and a frontend
    that may have no notion of Django models at all.

    **Name every key you want invalidated, including the collection.** Matching
    on the wire is exact and never by prefix, because a prefix rule guesses at a
    scheme this package does not own and fails both ways -- ``orders/1`` would
    match ``orders/11``. So a write to one row that should also refresh the list
    says both::

        resource_invalidation("orders", "orders/42", reason="place_order")

    The *host* may then match hierarchically in its own vocabulary, where the
    scheme is known and hierarchy is the point (TanStack query keys are built
    this way). That is the host's to interpret, not this package's to guess.

    ``reason`` is the tool or action that caused the write, carried for the
    host's logging and filtering. It is never interpreted here either.

    **The page it reaches is the one that started the run**, during the run.
    There is one ``StreamingHttpResponse`` per run and no channel to anybody
    else, so this cannot tell another user's open page that something moved --
    see the invalidation guide for why that is a different subsystem rather than
    a missing argument.
    """
    return CustomEvent(
        name=INVALIDATE_EVENT_NAME,
        value={"keys": list(keys), "reason": reason},
    )


__all__ = ["INVALIDATE_EVENT_NAME", "resource_invalidation"]
