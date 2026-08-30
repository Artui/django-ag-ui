"""``subagent_progress`` -- an AG-UI event reporting how a delegation is going."""

from __future__ import annotations

from typing import Any, Literal

from ag_ui.core import CustomEvent

SUBAGENT_EVENT_NAME = "ag_ui.subagent"
"""``name`` a client matches on to render a delegation's progress.

A **convention inside an extension point the protocol already provides**, not a
protocol extension: AG-UI defines the envelope and leaves ``name`` an open
string. A client that does not know this name ignores the event, which is the
graceful outcome and the whole reason the field is open -- a run that delegates
streams exactly as it did before, plus events nobody has to read.

## The value

Every event carries the same four keys, and the two tool-call phases add a
fifth::

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
  something the client has never heard of.
- ``agent`` is the sub-agent's name, as the parent model asked for it.
- ``phase`` is one of ``started``, ``tool_call``, ``tool_result``, ``finished``,
  ``failed``. One ``started`` opens a delegation and exactly one of ``finished``
  / ``failed`` closes it; any number of ``tool_call`` / ``tool_result`` pairs sit
  between.
- ``status`` is a rendered one-line summary. **A client that never expands a row
  needs nothing but this**, which is the point of sending it: the collapsed line
  costs no client-side assembly and no phase-to-wording table. A client that
  wants its own wording (a localised UI) builds it from the structured keys and
  ignores this one.
- ``tool`` is present on ``tool_call`` and ``tool_result`` only, and always has
  all three keys. ``ok`` is ``null`` on ``tool_call`` (the call has not returned
  yet), ``true`` on a result the child accepted, and ``false`` on one that came
  back as a retry. Sending the key as ``null`` rather than omitting it keeps the
  shape fixed, so a client can create the row on ``tool_call`` and update it in
  place on ``tool_result``.

## What is deliberately not here

**The child's failure text.** A ``failed`` event names the sub-agent and stops.
An exception's own words are written for an operator -- the same reasoning
``AgentSession`` applies when it withholds ``RUN_ERROR`` detail unless
``TOOL_FAILURE["INCLUDE_DETAIL"]`` opts in -- and this channel has no business
carrying them. Nothing is lost: whatever the delegation returns to the parent
model (a steering message, a retry) travels the ordinary ``TOOL_CALL_RESULT``
path and is rendered by the tool card this progress belongs to.

**The child's text output.** Progress is a status line, not a second transcript.

## Why ``CUSTOM``

The opposite of the choice ``chart_activity`` makes, and the difference is
**lifetime**, not taste. ``@ag-ui/client`` materialises an activity into a
``role: "activity"`` message; the transport persists the message list wholesale,
and the client's replay path re-fires it on every thread restore. That is right
for a chart, which is content and should come back. **Replayed progress is a
lie**: a run that finished last week would redraw "working, step 4 of 8" on
every reload of the thread.

**``ACTIVITY_SNAPSHOT`` is for content; ``CUSTOM`` is for an imperative.**
Progress is the second -- it has no meaning once acted on.

``STATE_SNAPSHOT`` / ``STATE_DELTA`` are out for a third reason: shared state
round-trips into the next ``RunAgentInput``, so progress placed there would be
echoed back to the model as though it were something the model had said.
"""


def subagent_progress(
    *,
    delegation_id: str,
    agent: str,
    phase: Literal["started", "tool_call", "tool_result", "finished", "failed"],
    status: str,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    ok: bool | None = None,
) -> CustomEvent:
    """One progress event for the delegation ``delegation_id``.

    See [`SUBAGENT_EVENT_NAME`][django_ag_ui.SUBAGENT_EVENT_NAME] for the wire
    contract this builds; the arguments are its keys, and ``tool_name`` is what
    decides whether the ``tool`` object is present at all.

    The keys are camelCase because AG-UI's own envelope is -- a client reading
    ``toolCallId`` off every other event should not have to read ``tool_call_id``
    off this one.
    """
    value: dict[str, Any] = {
        "delegationId": delegation_id,
        "agent": agent,
        "phase": phase,
        "status": status,
    }
    if tool_name is not None:
        value["tool"] = {"toolCallId": tool_call_id, "name": tool_name, "ok": ok}
    return CustomEvent(name=SUBAGENT_EVENT_NAME, value=value)


__all__ = ["SUBAGENT_EVENT_NAME", "subagent_progress"]
