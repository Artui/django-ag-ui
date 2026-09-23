"""``OutcomeAGUIAdapter`` — put a tool call's outcome on the AG-UI wire."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ag_ui.core import BaseEvent, Message, RunAgentInput, ToolCallResultEvent, ToolMessage
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    NativeToolReturnPart,
    OutputToolResultEvent,
    RetryPromptPart,
    ToolReturnPart,
)
from pydantic_ai.ui import UIEventStream
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream

from django_ag_ui.agent.stamp_outcome import stamp_outcome


class OutcomeAGUIAdapter(AGUIAdapter[Any, Any]):
    """An ``AGUIAdapter`` whose ``TOOL_CALL_RESULT`` events say how the call went.

    **The gap this closes.** AG-UI's ``TOOL_CALL_RESULT`` carries a ``content``
    string and nothing else, so a tool call that failed is byte-identical to one
    that succeeded and every client renders both the same way. Pydantic-AI knows
    the difference -- ``ToolReturnPart.outcome`` is one of ``"success"`` /
    ``"failed"`` / ``"denied"`` / ``"interrupted"`` -- and drops it on the floor
    at the protocol boundary, because there is nowhere in the event to put it.

    Upstream does carry it, on a ``REASONING_ENCRYPTED_VALUE`` event following
    the result: the only standard event whose reducer can attach continuity data
    to a ``ToolMessage``. That carrier is not usable here for two reasons. It is
    namespaced, opaque and documented as reasoning continuity rather than as a
    rendering signal, so reading it would mean a client parsing a blob it is told
    to treat as opaque. And this package's own ``FORWARD_REASONING=False`` filter
    drops every ``REASONING_*`` event, so the one deployment most likely to want
    a failure marked -- the one that turned chain-of-thought off -- is the one
    that could not see it.

    So the outcome is forwarded on the result event itself, twice: in the
    event's ``metadata``, the open slot AG-UI 1.0 declares and the only place a
    1.0 client keeps it, and as a top-level key for 0.x clients, which read
    that and nothing else -- every web component release up to 0.40 is one.
    ``stamp_outcome`` writes both and says why each is needed. **Absent means
    success**, which is what every server that does not do this emits, so a
    client reading either carrier needs no version negotiation and one that
    ignores both is unaffected.

    **Why a subclass rather than another stream wrapper.** Everything else this
    package adds to the stream (``inject_subagent_events``,
    ``stamp_approval_prompts``, ``drop_reasoning_events``) is a generator wrapped
    around ``AGUIAdapter.transform_stream``'s output, and none of them could do
    this: by then the ``ToolReturnPart`` is gone and only the events remain. A
    wrapper would have to guess the outcome from the content string -- exactly
    the guessing this exists to remove -- or rebuild the correlation from the
    native event stream, which does not survive contact with native tool returns
    (their AG-UI ``tool_call_id`` is a rewritten, prefixed id). ``build_event_stream``
    is the adapter's documented seam and is where the part and the event it
    produced are in the same frame, so the correlation is an identity rather than
    a lookup.

    **The stored copy says it too.** A thread is persisted by dumping the run's
    messages, and a client that replays it from the server reads that dump rather
    than the events it saw. ``dump_messages`` is overridden for the same reason
    the stream is, so a refused call does not come back as a completed one after
    a reload.
    """

    def build_event_stream(self) -> UIEventStream[RunAgentInput, BaseEvent, Any, Any]:
        """Build the outcome-forwarding event stream.

        Mirrors ``AGUIAdapter.build_event_stream`` exactly but for the class it
        instantiates. Restating the three constructor arguments is the coupling
        this override buys, and it is the smallest one available: upstream
        offers no "which class" hook, and re-classing the instance ``super()``
        returns would be worse. The signature has been byte-identical across
        every 2.x release, and a new argument would show up as a stream missing
        a setting rather than as an error, so this is the line to check when
        pydantic-ai's AG-UI adapter changes shape.
        """
        return _OutcomeEventStream(
            self.run_input, accept=self.accept, ag_ui_version=self.ag_ui_version
        )

    @classmethod
    def dump_messages(cls, messages: Sequence[ModelMessage], **kwargs: Any) -> list[Message]:
        """``AGUIAdapter.dump_messages``, each tool result stamped as the stream stamps it.

        Upstream's dump keeps a non-success outcome on ``error`` and on the
        ``encryptedValue`` carrier -- the same opaque envelope the class
        docstring declines to hand a client on the stream, and ``error`` alone
        cannot tell ``denied`` from ``failed``, or either from a retry prompt,
        which upstream also writes an ``error`` for. Both are left as upstream
        wrote them, since that is what ``load_messages`` reads the outcome back
        from, and the outcome is added beside them on the carriers the live
        ``TOOL_CALL_RESULT`` used: a stored thread replays the way it streamed.

        Correlated by ``tool_call_id``, which the dump keeps verbatim for a
        function or output tool. A call's id is unique within a thread; the web
        component keys a replayed tool card by it, so a thread where it was not
        would already replay wrongly. A provider-executed tool's return is not
        matched: its dumped id is rewritten by a rule upstream keeps private, and
        no model pydantic-ai ships reports a native return as anything but a
        success, so there is nothing to match yet. Should one start to, it is
        stored as upstream wrote it, where the stream would have marked it.

        ``**kwargs`` are upstream's keyword arguments, passed through untouched
        so that none of their defaults is restated here.
        """
        outcomes = _outcomes_by_call(messages)
        dumped = super().dump_messages(messages, **kwargs)
        return [_with_stored_outcome(message, outcomes) for message in dumped]


class _OutcomeEventStream(AGUIEventStream[Any, Any]):
    """``AGUIEventStream`` that stamps each tool result with its part's outcome.

    Three handlers, because three of them emit a ``TOOL_CALL_RESULT``: a function
    tool's result, an output tool's result, and a provider-executed (native)
    tool's return. Each delegates to the base implementation and marks what comes
    back, so nothing about how the event is built is duplicated here -- the
    content encoding, the message-id rules and the reasoning carrier all stay
    upstream's.
    """

    def handle_function_tool_result(
        self, event: FunctionToolResultEvent
    ) -> AsyncIterator[BaseEvent]:
        return _with_outcome(
            super().handle_function_tool_result(event), _forwardable_outcome(event.part)
        )

    def handle_output_tool_result(self, event: OutputToolResultEvent) -> AsyncIterator[BaseEvent]:
        return _with_outcome(
            super().handle_output_tool_result(event), _forwardable_outcome(event.part)
        )

    def handle_builtin_tool_return(self, part: NativeToolReturnPart) -> AsyncIterator[BaseEvent]:
        return _with_outcome(super().handle_builtin_tool_return(part), _forwardable_outcome(part))


def _forwardable_outcome(
    part: ToolReturnPart | NativeToolReturnPart | RetryPromptPart,
) -> str | None:
    """The part's outcome when it is worth putting on the wire, else ``None``.

    The value is forwarded verbatim rather than mapped onto a vocabulary of our
    own. ``outcome`` is pydantic-ai's field and its four values are pydantic-ai's;
    translating them here would mean this package deciding which of upstream's
    answers a client is allowed to see, and being wrong about a fifth one the day
    it is added. The rendering contract clients are given says an unrecognised
    value is treated as success, which is the same rule as an absent one.

    Two arms return ``None``, and each is held by its own test --
    ``test_a_retry_prompt_is_not_a_failure`` and
    ``test_a_successful_call_carries_no_outcome_field`` -- because a single
    ``or``-chain here would report full branch coverage with either condition
    deleted.
    """
    # A ``RetryPromptPart`` has no ``outcome`` at all, and should not acquire
    # one: a retry is not a failed call, it is a call the model is about to make
    # again with corrected arguments.
    if isinstance(part, RetryPromptPart):
        return None
    # Absent means success, so the default is never written. That is what keeps
    # this additive: every existing server omits the field, and a client cannot
    # tell one of them apart from this one on a call that worked.
    if part.outcome == "success":
        return None
    return part.outcome


def _outcomes_by_call(messages: Sequence[ModelMessage]) -> dict[str, str]:
    """Each function or output tool call's forwardable outcome, keyed by call id.

    Only calls whose outcome would be stamped on the stream appear, so a lookup
    that misses means success -- the same rule as an absent field. A
    ``NativeToolReturnPart`` is a sibling of ``ToolReturnPart`` rather than a
    subclass, so the ``isinstance`` leaves it out; see ``dump_messages`` for why.
    """
    outcomes: dict[str, str] = {}
    for message in messages:
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            outcome = _forwardable_outcome(part)
            if outcome is not None:
                outcomes[part.tool_call_id] = outcome
    return outcomes


def _with_stored_outcome(message: Message, outcomes: Mapping[str, str]) -> Message:
    """``message``, stamped when it is the result of a call ``outcomes`` names."""
    if not isinstance(message, ToolMessage):
        return message
    outcome = outcomes.get(message.tool_call_id)
    if outcome is None:
        return message
    return stamp_outcome(message, outcome)


async def _with_outcome(
    events: AsyncIterator[BaseEvent], outcome: str | None
) -> AsyncIterator[BaseEvent]:
    """Forward ``events``, stamping ``outcome`` onto every tool result among them.

    The stamp itself is ``stamp_outcome``, which copies rather than assigns, so
    the instance upstream built is left alone. It is a module of its own rather
    than a private helper here because a wire-fixture recorder outside this
    repository calls it, and a fixture recorded through a copy of this logic
    would agree with the copy rather than with the server.

    One handler's events all describe one tool call, so "every result event"
    and "the result event" are the same set in practice -- a tool that returns
    an AG-UI event of its own is naming the same call.

    Both conditions below are held independently, which an ``and``-chain does not
    get for free: deleting either leaves 100% line and branch coverage, because
    the surviving expression is still taken both ways. Delete ``outcome is not
    None`` and ``test_a_successful_call_carries_no_outcome_field`` and
    ``test_a_retry_prompt_is_not_a_failure`` fail -- a stamped ``None`` encodes as
    ``"outcome":null``, which is not the same as an absent field. Delete the
    ``isinstance`` and ``test_the_events_around_a_failed_result_are_untouched``
    fails.
    """
    async for event in events:
        if outcome is not None and isinstance(event, ToolCallResultEvent):
            event = stamp_outcome(event, outcome)
        yield event


__all__ = ["OutcomeAGUIAdapter"]
