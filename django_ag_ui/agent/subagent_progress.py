"""``subagent_progress`` -- an AG-UI event reporting a step inside a delegation."""

from __future__ import annotations

from typing import Any, Literal

from ag_ui.core import CustomEvent

from django_ag_ui.agent.event_timestamp import event_timestamp

SUBAGENT_EVENT_NAME = "ag_ui.subagent"
"""``name`` a client matches on to render one step a delegated sub-agent took.

A **convention inside an extension point the protocol already provides**, not a
protocol extension: AG-UI defines the envelope and leaves ``name`` an open
string. A client that does not know this name ignores the event, which is the
graceful outcome and the whole reason the field is open -- a run that delegates
streams exactly as it did before, plus events nobody has to read.

## Half a contract, and the half it is

A delegation's *lifetime* is no longer described here. AG-UI 0.1.21 gave a
sub-agent a first-class lifecycle, so ``SUBAGENT_STARTED`` opens a delegation
and ``SUBAGENT_FINISHED`` / ``SUBAGENT_ERROR`` closes it, built by
``subagent_lifecycle``. What stayed on this carrier is what the child does in
between: one event per tool call it makes and one per result it gets back.

## The value

Every event carries the same five keys::

    {
      "delegationId": "call_abc123",
      "agent": "researcher",
      "phase": "tool_call",
      "status": "researcher: calling search_docs",
      "tool": {"toolCallId": "call_def456", "name": "search_docs", "ok": null}
    }

- ``delegationId`` is the parent's ``delegate_task`` **tool call id** -- the
  ``toolCallId`` the client already received on ``TOOL_CALL_START`` and already
  drew a tool card for. That is what makes this an *augmentation* of a card on
  screen rather than a second row beside it, and it is why the id is the
  parent's call rather than the child's run id: the child's run id names
  something the client has never heard of. It is also the value the lifecycle
  events carry as ``parentToolCallId``, which is what joins the two carriers.
- ``agent`` is the sub-agent's name, as the parent model asked for it.
- ``phase`` is ``tool_call`` or ``tool_result``. Any number of the pair sit
  between the ``SUBAGENT_STARTED`` that opens the delegation and the
  ``SUBAGENT_FINISHED`` / ``SUBAGENT_ERROR`` that closes it.
- ``status`` is a rendered one-line summary. **A client that never expands a row
  needs nothing but this**, which is the point of sending it: the collapsed line
  costs no client-side assembly and no phase-to-wording table. A client that
  wants its own wording (a localised UI) builds it from the structured keys and
  ignores this one.
- ``tool`` always has all three keys. ``ok`` is ``null`` on ``tool_call`` (the
  call has not returned yet), ``true`` on a result the child accepted, and
  ``false`` on one it did not -- which pydantic-ai surfaces to the child as a
  retry, and which the ``status`` line calls *failed*, because that is what a
  reader watching the row sees happen. A client rendering its own wording should
  follow ``status``; ``ok`` is the machine-readable half of the same fact.
  Sending the key as ``null`` rather than omitting it keeps the shape fixed, so
  a client can create the row on ``tool_call`` and update it in place on
  ``tool_result``.

  Note that the ``null`` survives on the wire, and it is worth knowing why it
  is safe to rely on. From 0.1.21 the protocol omits an unset optional field
  rather than serialising it as ``null`` -- but that applies to declared *model
  fields*, and ``ok`` is a value inside a plain ``dict``, which it leaves alone.

**The child's text output is deliberately absent.** Progress is a status line,
not a second transcript.

## Why ``CUSTOM``, still, for these two

The lifecycle moved to the protocol's own events and these did not, and the
reason is the one that chose ``CUSTOM`` in the first place: **lifetime**.

The protocol *does* have a way to say "the child called a tool" -- an ordinary
``TOOL_CALL_START`` tagged with ``subagentRunId``. But ``@ag-ui/client``
materialises those into ``agent.messages`` exactly as it does the parent's own,
the transport persists the message list wholesale, and the replay path re-fires
it on every thread restore. **Replayed progress is a lie**: a run that finished
last week would redraw "working, step 4 of 8" on every reload of the thread.
The three lifecycle events have no such problem -- the client dispatches them to
callbacks and never pushes them into ``messages`` -- which is precisely why they
were adoptable and these are not.

**``ACTIVITY_SNAPSHOT`` is for content; ``CUSTOM`` is for an imperative.**
Progress is the second -- it has no meaning once acted on. That is also the
choice ``chart_activity`` makes in the other direction, because a chart is
content and should come back.

``STATE_SNAPSHOT`` / ``STATE_DELTA`` are out for a third reason: shared state
round-trips into the next ``RunAgentInput``, so progress placed there would be
echoed back to the model as though it were something the model had said.
"""


def subagent_progress(
    *,
    delegation_id: str,
    agent: str,
    phase: Literal["tool_call", "tool_result"],
    status: str,
    tool_call_id: str,
    tool_name: str,
    ok: bool | None = None,
) -> CustomEvent:
    """One step event for the delegation ``delegation_id``.

    See [`SUBAGENT_EVENT_NAME`][django_ag_ui.SUBAGENT_EVENT_NAME] for the wire
    contract this builds; the arguments are its keys.

    The tool fields are required rather than optional, unlike the version of
    this that also carried the lifecycle: every phase left here *is* a tool
    phase, so a step without a tool is not a case to degrade but a caller bug.

    The keys are camelCase because AG-UI's own envelope is -- a client reading
    ``toolCallId`` off every other event should not have to read ``tool_call_id``
    off this one.
    """
    value: dict[str, Any] = {
        "delegationId": delegation_id,
        "agent": agent,
        "phase": phase,
        "status": status,
        "tool": {"toolCallId": tool_call_id, "name": tool_name, "ok": ok},
    }
    return CustomEvent(name=SUBAGENT_EVENT_NAME, value=value, timestamp=event_timestamp())


__all__ = ["SUBAGENT_EVENT_NAME", "subagent_progress"]
