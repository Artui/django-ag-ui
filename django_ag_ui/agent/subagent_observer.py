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
    # Deprecated from pydantic-ai 2.60, where a retry became the call's own
    # ``ToolReturnPart``, and still what every release below it emits for one,
    # the declared floor among them. Named until the floor passes 2.60, so
    # every use carries the same suppression.
    RetryPromptPart,  # ty: ignore[deprecated]
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
    """Wraps a ``SubAgents`` capability and reports each delegation as it happens."""

    def __init__(self, wrapped: Any) -> None:
        _install_handler(wrapped, self._forward_child_events)
        super().__init__(wrapped)

    @property
    def _delegate_tool_name(self) -> str:
        """The wrapped capability's delegate-tool name, read on access."""
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
        """Announce the delegation this call is, then run it."""
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
            _emit(_lifecycle(delegation, phase="failed"))
            raise
        else:
            _emit(_lifecycle(delegation, phase="finished"))
            return result
        finally:
            _DELEGATION.reset(token)

    async def _forward_child_events(self, ctx: Any, events: AsyncIterable[Any]) -> None:
        """The ``SubAgents.event_stream_handler``: report the child's tool calls."""
        delegation = _DELEGATION.get()
        async for event in events:
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
                part = event.part
                ok = not (
                    isinstance(part, RetryPromptPart)  # ty: ignore[deprecated]
                    or part.outcome == "retried"
                )
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
    """Queue one event for the run currently streaming, if there is one."""
    sink = SUBAGENT_SINK.get()
    if sink is None:
        return
    sink.put_nowait(event)


def _install_handler(wrapped: Any, handler: Any) -> None:
    """Point the wrapped capability's ``event_stream_handler`` at ``handler``."""
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
