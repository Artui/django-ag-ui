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

A tool step carries the same five keys::

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
- ``phase`` is ``tool_call`` or ``tool_result`` on a tool step (the task phases
  are below). Any number of the pair sit
  between the ``SUBAGENT_STARTED`` that opens the delegation and the
  ``SUBAGENT_FINISHED`` / ``SUBAGENT_ERROR`` that closes it.
- ``status`` is a rendered one-line summary. **A client that never expands a row
  needs nothing but this**, which is the point of sending it: the collapsed line
  costs no client-side assembly and no phase-to-wording table. A client that
  wants its own wording (a localised UI) builds it from the structured keys and
  ignores this one.
- ``tool`` has all three keys on a tool step and is ``null`` on a task step. ``ok`` is
  ``null`` on ``tool_call`` (the call has not returned yet), ``true`` on a result the
  child accepted, and ``false`` on one it did not -- which pydantic-ai surfaces to the
  child as a retry, and which the ``status`` line calls *failed*, because that is what a
  reader watching the row sees happen. A client rendering its own wording should follow
  ``status``; ``ok`` is the machine-readable half of the same fact. Sending the key as
  ``null`` rather than omitting it keeps the shape fixed, so a client can create the row
  on ``tool_call`` and update it in place on ``tool_result``.

  Note that the ``null`` survives on the wire, and it is worth knowing why it
  is safe to rely on. From 0.1.21 the protocol omits an unset optional field
  rather than serialising it as ``null`` -- but that applies to declared *model
  fields*, and ``ok`` is a value inside a plain ``dict``, which it leaves alone.

**The child's text output is deliberately absent.** Progress is a status line,
not a second transcript.

## Task-control steps

Under ``SubAgents(task_control=...)`` a task's own moments are steps too:
``phase`` is ``backgrounded``, ``steered``, ``question``, ``answered``,
``cancel_requested`` or ``retried``, ``tool`` is ``null``, and a sixth key,
``task``, names the task (``taskId``) plus what that moment adds -- ``reason``,
``questionId``, ``forced`` or ``attempt``. The steering message, the question and
the answer are not sent: the step says that it happened, not what was said.

## Why ``CUSTOM`` beside the tagged tool calls

The child's tool calls also go out on the protocol's own vocabulary, ordinary
``TOOL_CALL_*`` events tagged with ``subagentRunId`` (``subagent_tool_calls``),
so a client that renders sub-agent tool calls natively has them. They do not
replace these steps, and the reason is **lifetime**: ``@ag-ui/client``
materialises tagged tool calls into ``agent.messages`` as it does the parent's
own, and the replay path re-fires the message list on every thread restore.
**Replayed progress is a lie**: a run that finished last week would redraw
"working, step 4 of 8" on every reload. A ``CUSTOM`` step is dispatched to
callbacks and never pushed into ``messages``, so it is the carrier for a live
status line.

**``ACTIVITY_SNAPSHOT`` is for content; ``CUSTOM`` is for an imperative.**
Progress is the second -- it has no meaning once acted on. That is also the
choice ``chart_activity`` makes in the other direction, because a chart is
content and should come back.

``STATE_SNAPSHOT`` / ``STATE_DELTA`` are out for a third reason: shared state
round-trips into the next ``RunAgentInput``, so progress placed there would be
echoed back to the model as though it were something the model had said.
"""


_StepPhase = Literal[
    "tool_call",
    "tool_result",
    "backgrounded",
    "steered",
    "question",
    "answered",
    "cancel_requested",
    "retried",
]
"""The ``phase`` values a step event carries: two tool phases, then the task-control ones."""


def subagent_progress(
    *,
    delegation_id: str,
    agent: str,
    phase: _StepPhase,
    status: str,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    ok: bool | None = None,
    task: dict[str, Any] | None = None,
) -> CustomEvent:
    """One step event for the delegation ``delegation_id``."""
    value: dict[str, Any] = {
        "delegationId": delegation_id,
        "agent": agent,
        "phase": phase,
        "status": status,
        "tool": None
        if tool_call_id is None
        else {"toolCallId": tool_call_id, "name": tool_name, "ok": ok},
    }
    if task is not None:
        value["task"] = task
    return CustomEvent(name=SUBAGENT_EVENT_NAME, value=value, timestamp=event_timestamp())


__all__ = ["SUBAGENT_EVENT_NAME", "subagent_progress"]
