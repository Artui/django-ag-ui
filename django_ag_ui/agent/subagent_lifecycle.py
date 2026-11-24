from __future__ import annotations

from typing import Literal

from ag_ui.core import (
    SubagentErrorEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
)

from django_ag_ui.agent.event_timestamp import event_timestamp

SubAgentLifecycleEvent = SubagentStartedEvent | SubagentFinishedEvent | SubagentErrorEvent
"""What a delegation's three lifecycle moments are carried on.

The protocol's own vocabulary, not a convention of ours -- which is the whole
difference between this module and its sibling
[`subagent_progress`][django_ag_ui.SUBAGENT_EVENT_NAME]. AG-UI 0.1.21 gave a
delegation a first-class lifecycle, so opening and closing one is no longer a
thing this transport has to invent a ``name`` for; a client that speaks AG-UI
already knows what these mean without knowing anything about us.

**What the child does in between rides two carriers.** Its tool calls go out as
the protocol's ``TOOL_CALL_*`` events tagged with ``subagentRunId`` and, beside
them, as ``CUSTOM`` steps: ``@ag-ui/client`` materialises the tagged events into
the message list, which the transport replays on a thread restore, and progress
that replays is a lie about a run that is over. See ``subagent_progress``.

A delegation closes with ``SUBAGENT_ERROR`` both when it fails and when it is
cancelled; a cancelled task's close carries ``code="cancelled"``.

The three lifecycle events do **not** have that problem, and that is what makes
them adoptable: ``@ag-ui/client`` dispatches them to subscriber callbacks and
never pushes them into ``agent.messages``, so they are as ephemeral as the
``CUSTOM`` events they replace.
"""


def subagent_run_id(delegation_id: str) -> str:
    """The ``subagentRunId`` naming the child run that ``delegation_id`` spawned."""
    return f"subagent-{delegation_id}"


def subagent_lifecycle(
    *,
    delegation_id: str,
    agent: str,
    phase: Literal["started", "finished", "failed", "cancelled"],
) -> SubAgentLifecycleEvent:
    """The event opening or closing the delegation made by ``delegation_id``."""
    run_id = subagent_run_id(delegation_id)
    timestamp = event_timestamp()
    if phase == "started":
        return SubagentStartedEvent(
            subagent_run_id=run_id,
            name=agent,
            parent_tool_call_id=delegation_id,
            timestamp=timestamp,
        )
    if phase == "finished":
        return SubagentFinishedEvent(subagent_run_id=run_id, timestamp=timestamp)
    if phase == "cancelled":
        return SubagentErrorEvent(
            subagent_run_id=run_id,
            message=f"{agent} was cancelled",
            code="cancelled",
            timestamp=timestamp,
        )
    return SubagentErrorEvent(
        subagent_run_id=run_id,
        message=f"{agent} failed",
        timestamp=timestamp,
    )


__all__ = ["SubAgentLifecycleEvent", "subagent_lifecycle", "subagent_run_id"]
