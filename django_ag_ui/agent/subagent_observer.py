"""``SubAgentObserver`` -- make a delegated sub-agent's work visible to the client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, cast

from ag_ui.core import BaseEvent
from django.core.exceptions import ImproperlyConfigured
from pydantic_ai.capabilities import WrapperCapability
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RetryPromptPart,
)

from django_ag_ui.agent.subagent_lifecycle import subagent_lifecycle
from django_ag_ui.agent.subagent_progress import subagent_progress

# The per-run channel the observer announces onto, drained by
# ``inject_subagent_events`` while it forwards the AG-UI stream.
#
# A ``ContextVar`` rather than state on the observer, which is built once at
# configuration time and then serves every request: an instance-level channel
# would interleave concurrent runs into each other's transcripts. Context
# variables are per-task, and a delegation run outside this transport -- from a
# management command, a worker, a test -- finds no channel and announces
# nothing rather than erroring.
#
# An ``asyncio.Queue`` rather than the plain list ``COMPACTION_SINK`` and
# ``INVALIDATION_SINK`` use, and the difference is the whole point of this
# feature. A list is drained by the injector as the *upstream* stream advances,
# and upstream emits nothing at all while a tool executes -- so a list would
# hold every one of a delegation's progress events until the delegation had
# already finished, which is the stall this exists to fix. A queue can be
# awaited, so the injector can race it against the next upstream event.
SUBAGENT_SINK: ContextVar[asyncio.Queue[BaseEvent] | None] = ContextVar(
    "django_ag_ui_subagent_sink", default=None
)


@dataclass(frozen=True)
class _Delegation:
    """The delegation a child run belongs to, as the client will key it."""

    delegation_id: str
    agent: str


# The delegation currently executing on this task. Set by ``wrap_tool_execute``
# before the child run starts and read by the event-stream handler once it does.
#
# This is the only route from a child event back to the parent's tool call:
# the handler is given the *child's* ``RunContext``, which knows its own fresh
# run id and nothing about the call that spawned it. Setting a context variable
# inside the tool-call coroutine is what carries the correspondence across --
# and because pydantic-ai runs concurrent tool calls in their own tasks, each
# of two parallel delegations writes into its own copy of the context rather
# than over the other's.
_DELEGATION: ContextVar[_Delegation | None] = ContextVar(
    "django_ag_ui_subagent_delegation", default=None
)


class SubAgentObserver(WrapperCapability[Any]):
    """Wraps a ``SubAgents`` capability and reports each delegation as it happens.

    A delegated child runs to completion inside one ``delegate_task`` tool call,
    and a tool call emits nothing between its arguments and its result. So a
    parent that hands a long task to a sub-agent shows a tool card that simply
    sits there -- for a minute, for five -- with no way to tell a working run
    from a wedged one. Wrapping the capability is the seam for saying so:

        capabilities=[SubAgentObserver(SubAgents(agents=[...], agent_folders=None))]

    Opt-in by construction: passing ``SubAgents`` unwrapped emits nothing, and
    costs nothing.

    What reaches the client rides **two carriers, on purpose**. The delegation's
    own lifetime goes on the protocol's ``SUBAGENT_STARTED`` / ``_FINISHED`` /
    ``_ERROR`` events, built by ``subagent_lifecycle``; each tool call the child
    makes goes on a ``CUSTOM`` event, whose wire contract is
    [`SUBAGENT_EVENT_NAME`][django_ag_ui.SUBAGENT_EVENT_NAME]. Both key on the
    parent's own ``delegate_task`` tool call id -- as ``parentToolCallId`` and as
    ``delegationId`` respectively -- so a client augments the card it already
    drew rather than opening a second row beside it.

    The split is not a transitional state. Moving the steps to the protocol's
    own vocabulary would mean ordinary ``TOOL_CALL_*`` events tagged with
    ``subagentRunId``, and those are materialised into the persisted message
    list and replayed on every thread restore -- which would redraw a finished
    run's progress as though it were live. ``subagent_progress`` carries the
    full reasoning.

    **The observer installs itself onto the capability you hand it.** Reporting
    a child's tool calls needs ``SubAgents.event_stream_handler``, which only the
    capability that starts the child run can pass on, so construction sets that
    field on the wrapped instance. Two consequences worth stating rather than
    discovering: wrapping a ``SubAgents`` that *already* carries a handler is
    refused (silently replacing it would lose whatever it was for), and the
    instance you passed is the one that changed. The second is harmless in a way
    worth knowing -- the installed handler announces onto
    ``SUBAGENT_SINK``, which no run outside this transport's stream ever binds,
    so the same instance reused at an unwrapped endpoint behaves exactly as it
    did before.

    Subclassing ``WrapperCapability`` (pydantic-ai's supported wrapper, analogous
    to ``WrapperToolset``) rather than hand-rolling a proxy keeps the rest of the
    capability protocol intact -- ordering, the ``has_*`` hook-introspection
    flags and every other lifecycle method delegate untouched.
    """

    def __init__(self, wrapped: Any) -> None:
        _install_handler(wrapped, self._forward_child_events)
        super().__init__(wrapped)

    @property
    def _delegate_tool_name(self) -> str:
        """The wrapped capability's delegate-tool name, read on access.

        A wrapper is rebound as a shallow copy when what it wraps returns a
        fresh instance for a run, and a value cached at construction would then
        describe the capability that was replaced. ``SubAgents.tool_name`` is
        configurable, so reading it late is the difference between observing an
        endpoint that renamed the tool and observing nothing at all.

        Cast because ``wrapped`` is typed as the capability protocol, which has
        no ``tool_name``. That the field is there is exactly what construction
        refused to proceed without.
        """
        return str(cast("Any", self.wrapped).tool_name)

    async def wrap_tool_execute(
        self,
        ctx: Any,
        *,
        call: Any,
        tool_def: Any,
        args: dict[str, Any],
        handler: Any,
    ) -> Any:
        """Announce the delegation this call is, then run it.

        This hook fires for **every** tool in the parent run, not only the ones
        the wrapped capability contributed -- pydantic-ai composes one root
        capability and asks it about each call -- so the delegate tool is picked
        out by name and everything else is delegated untouched.

        **Every exit path closes the delegation it opened, cancellation
        included** -- which reverses an earlier choice here, and the reason is
        worth keeping. Not announcing on ``asyncio.CancelledError`` was defended
        as "a cancelled run is a client that has gone away, and there is nobody
        left to tell", and that holds only when the whole *run* was cancelled. A
        single tool call can be cancelled while the run continues, and under the
        protocol's own lifecycle an unclosed delegation is not merely a missing
        line: ``@ag-ui/client`` refuses ``RUN_FINISHED`` while any delegation is
        still open, so the omission would take down the run it was trying not to
        disturb. Announcing into a sink nobody is draining costs nothing, which
        makes the safe direction the cheap one.
        """
        if call.tool_name != self._delegate_tool_name:
            return await super().wrap_tool_execute(
                ctx, call=call, tool_def=tool_def, args=args, handler=handler
            )
        delegation = _Delegation(
            delegation_id=call.tool_call_id,
            agent=str(args.get("agent_name", "")),
        )
        token = _DELEGATION.set(delegation)
        _emit(_lifecycle(delegation, phase="started"))
        try:
            result = await super().wrap_tool_execute(
                ctx, call=call, tool_def=tool_def, args=args, handler=handler
            )
        except BaseException:
            # ``BaseException`` rather than ``Exception``, so that a cancelled
            # tool call closes its delegation too -- see the note above. Named,
            # and nothing more: the exception's own words are written for an
            # operator, and what the *model* is told travels the ordinary tool
            # result, which the client renders on the card this belongs to.
            _emit(_lifecycle(delegation, phase="failed"))
            raise
        else:
            _emit(_lifecycle(delegation, phase="finished"))
            return result
        finally:
            _DELEGATION.reset(token)

    async def _forward_child_events(self, ctx: Any, events: AsyncIterable[Any]) -> None:
        """The ``SubAgents.event_stream_handler``: report the child's tool calls.

        Only the child's *function* tool calls are forwarded. Its text and its
        output-tool traffic are left alone: progress is a status line, not a
        second transcript, and an output tool is how a child returns its answer
        rather than something it did along the way.

        Consuming the stream is this handler's obligation -- upstream hands it
        over and does not iterate it itself.
        """
        delegation = _DELEGATION.get()
        async for event in events:
            # ``None`` cannot happen on the path that installs this handler
            # (only a delegation starts a child run), and is checked anyway
            # because a handler is a plain callable that anything can call.
            if delegation is None:
                continue
            if isinstance(event, FunctionToolCallEvent):
                _emit(
                    subagent_progress(
                        delegation_id=delegation.delegation_id,
                        agent=delegation.agent,
                        phase="tool_call",
                        status=f"{delegation.agent}: calling {event.part.tool_name}",
                        tool_call_id=event.tool_call_id,
                        tool_name=event.part.tool_name,
                    )
                )
            elif isinstance(event, FunctionToolResultEvent):
                # A ``RetryPromptPart`` is the child's tool telling the child's
                # model to try again -- the one outcome distinguishable from
                # here without reading the result's contents.
                ok = not isinstance(event.part, RetryPromptPart)
                outcome = "returned" if ok else "failed"
                _emit(
                    subagent_progress(
                        delegation_id=delegation.delegation_id,
                        agent=delegation.agent,
                        phase="tool_result",
                        status=f"{delegation.agent}: {event.part.tool_name} {outcome}",
                        tool_call_id=event.tool_call_id,
                        tool_name=event.part.tool_name,
                        ok=ok,
                    )
                )


def _lifecycle(
    delegation: _Delegation, *, phase: Literal["started", "finished", "failed"]
) -> BaseEvent:
    """The protocol event opening or closing ``delegation``."""
    return subagent_lifecycle(
        delegation_id=delegation.delegation_id,
        agent=delegation.agent,
        phase=phase,
    )


def _emit(event: BaseEvent) -> None:
    """Queue one event for the run currently streaming, if there is one.

    The no-sink case is the ordinary one outside this transport -- a delegation
    driven from a management command, a worker or a test binds no channel -- and
    it is why every caller can announce unconditionally.
    """
    sink = SUBAGENT_SINK.get()
    if sink is None:
        return
    sink.put_nowait(event)


def _install_handler(wrapped: Any, handler: Any) -> None:
    """Point the wrapped capability's ``event_stream_handler`` at ``handler``.

    Refuses anything that is not shaped like a ``SubAgents`` -- the field it
    needs is the one that carries child events out, and a capability without it
    would wrap cleanly and then report only the delegation's start and end,
    which is the failure that looks like it works.
    """
    if not hasattr(wrapped, "event_stream_handler") or not hasattr(wrapped, "tool_name"):
        raise ImproperlyConfigured(
            "SubAgentObserver(...) takes a pydantic-ai-harness SubAgents capability; "
            f"{type(wrapped).__name__} has no event_stream_handler / tool_name to observe. "
            "Wrap the SubAgents instance itself, not the agent or the toolset."
        )
    if wrapped.event_stream_handler is not None:
        raise ImproperlyConfigured(
            "SubAgentObserver(...) needs SubAgents.event_stream_handler, and this one is "
            "already set. Observing would replace it and lose whatever it was for; call "
            "your own handler from a SubAgentObserver subclass instead."
        )
    wrapped.event_stream_handler = handler


__all__ = ["SUBAGENT_SINK", "SubAgentObserver"]
