"""``subagent_lifecycle`` -- an AG-UI event opening or closing one delegation."""

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

**The split is deliberate, and it is drawn where the protocol draws it.** These
three events open and close a delegation. What the child does *in between* --
its individual tool calls -- has no ephemeral carrier upstream: the protocol
would have those be ordinary ``TOOL_CALL_*`` events tagged with
``subagentRunId``, and ``@ag-ui/client`` materialises those into the message
list, which the transport persists and replays. Progress that replays is a lie
about a run that is over, which is the property the ``CUSTOM`` carrier was
chosen for in the first place. So the steps stay on ``CUSTOM`` and only the
lifecycle moves. See ``subagent_progress`` for the other half.

The three lifecycle events do **not** have that problem, and that is what makes
them adoptable: ``@ag-ui/client`` dispatches them to subscriber callbacks and
never pushes them into ``agent.messages``, so they are as ephemeral as the
``CUSTOM`` events they replace.
"""


def subagent_run_id(delegation_id: str) -> str:
    """The ``subagentRunId`` naming the child run that ``delegation_id`` spawned.

    Derived from the parent's ``delegate_task`` tool call id rather than minted,
    and both halves of that are load-bearing.

    *Derived*, because the protocol requires the id to be unique per invocation
    and refuses to see one reused inside a run -- and a tool call id is already
    exactly that, so deriving inherits the guarantee instead of restating it.
    Determinism also keeps the checked-in wire fixture byte-stable: a random id
    would have to be canonicalised away, and every field canonicalised out of
    that file is a field it no longer demonstrates.

    *Distinct*, because they are different things and the protocol keeps them in
    different fields. A client correlating the two reads
    ``parentToolCallId`` off ``SUBAGENT_STARTED``; it must never recover the
    delegation by stripping this prefix, which is why the prefix exists only to
    make the two visibly different in a log rather than to be parsed.
    """
    return f"subagent-{delegation_id}"


def subagent_lifecycle(
    *,
    delegation_id: str,
    agent: str,
    phase: Literal["started", "finished", "failed"],
) -> SubAgentLifecycleEvent:
    """The event opening or closing the delegation made by ``delegation_id``.

    ``delegation_id`` is the parent's own ``delegate_task`` tool call id -- the
    ``toolCallId`` the client received on ``TOOL_CALL_START`` and already drew a
    card for. It travels as ``parentToolCallId`` on the opening event, which is
    the protocol's field for exactly this shape ("agents as tools"), and is what
    lets a client augment the card already on screen rather than open a second
    row beside it.

    Exactly one ``started`` opens a delegation and exactly one ``finished`` or
    ``failed`` closes it. That is not a house rule: ``@ag-ui/client`` verifies
    it, refusing a reused id and refusing ``RUN_FINISHED`` while any delegation
    is still open. A caller therefore owes a close on **every** exit path,
    cancellation included.

    **``failed`` carries no exception text**, and the required ``message`` says
    only which sub-agent failed. An exception's own words are written for an
    operator -- the same reasoning ``AgentSession`` applies when it withholds
    ``RUN_ERROR`` detail unless ``TOOL_FAILURE["INCLUDE_DETAIL"]`` opts in.
    Nothing is lost: what the delegation returned to the parent model travels
    the ordinary ``TOOL_CALL_RESULT`` and is rendered on the card this belongs
    to.

    Note what is *absent* from the events this builds rather than sent as null.
    The protocol's optional fields are omitted from the JSON entirely from
    0.1.21, and its own client refuses several of them as an explicit ``null``
    -- so the floor that introduced these events is also the floor that made
    them serialise the way their reader demands.
    """
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
    return SubagentErrorEvent(
        subagent_run_id=run_id,
        message=f"{agent} failed",
        timestamp=timestamp,
    )


__all__ = ["SubAgentLifecycleEvent", "subagent_lifecycle", "subagent_run_id"]
