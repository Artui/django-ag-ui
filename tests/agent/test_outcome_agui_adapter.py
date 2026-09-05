from __future__ import annotations

import json
from typing import Any

from ag_ui.core import BaseEvent, ReasoningEncryptedValueEvent, ToolCallResultEvent
from ag_ui.encoder import EventEncoder
from django.test import RequestFactory
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.policy.audit.null_audit_logger import NullAuditLogger
from pydantic_ai import Agent, ToolFailed
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    OutputToolResultEvent,
    RetryPromptPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.outcome_agui_adapter import (
    OUTCOME_FIELD,
    OutcomeAGUIAdapter,
    _OutcomeEventStream,
)
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config

# --- driving the event stream directly ---------------------------------------
#
# The three handlers under test each turn one Pydantic-AI part into AG-UI
# events, and none of them needs a run to do it. Calling them on a bare
# ``_OutcomeEventStream`` is what makes the native-tool arm reachable at all --
# a provider-executed tool return needs a provider otherwise.


async def _drain(events: Any) -> list[BaseEvent]:
    return [event async for event in events]


def _stream() -> _OutcomeEventStream:
    """A standalone event stream, with no run behind it.

    ``None`` is passed explicitly rather than relying on the field's default,
    because there was not one at the declared pydantic-ai floor: ``run_input``
    only stopped being a required positional argument later in the 2.x line, and
    the base class documents ``None`` as the standalone-encoder case at every
    version. Dropping the argument passes locally and fails the floor job.
    """
    return _OutcomeEventStream(None)


def _tool_return(**kwargs: Any) -> ToolReturnPart:
    return ToolReturnPart(tool_name="t", content="c", tool_call_id="call-1", **kwargs)


def _extras(event: BaseEvent) -> dict[str, Any]:
    """The additive fields on ``event``, as a mapping to test membership against.

    Membership rather than ``getattr(event, "outcome", None)``, because *absent*
    is the load-bearing state and the two are not the same on the wire: a field
    stamped with ``None`` encodes as ``"outcome":null``, which is a value a
    client's schema has to accept and no other AG-UI server sends.

    This is not a hypothetical distinction. The first version of this suite read
    the field with ``getattr`` and defaulted to ``None`` -- and passed with the
    ``outcome is not None`` guard deleted from the stamping condition, because a
    stamped ``None`` read back identically to an absent field. The helper agreed
    with the bug.
    """
    return dict(event.__pydantic_extra__ or {})


async def test_a_failed_tool_result_carries_its_outcome() -> None:
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="failed"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert _extras(result)[OUTCOME_FIELD] == "failed"


async def test_a_denied_tool_result_carries_its_outcome() -> None:
    """A denial is not a failure, and the client renders it differently. The
    value is pydantic-ai's own and is forwarded verbatim rather than folded
    into ``failed``."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="denied"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert _extras(result)[OUTCOME_FIELD] == "denied"


async def test_a_successful_call_carries_no_outcome_field() -> None:
    """Absent means success, and that is what makes the field additive.

    Writing ``outcome="success"`` would make this server distinguishable from
    every other AG-UI server on a call that worked, for no gain. Holds both the
    ``== "success"`` arm of ``_forwardable_outcome`` and the ``outcome is not
    None`` half of the stamping condition -- delete either and the field appears,
    as ``"success"`` for the first and as ``null`` for the second.
    """
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="success"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert OUTCOME_FIELD not in _extras(result)
    # Asserted on the encoded event as well, because the wire is what a client
    # parses and the field is absent there or it is not absent at all.
    assert OUTCOME_FIELD not in EventEncoder().encode(result)


async def test_a_retry_prompt_is_not_a_failure() -> None:
    """A ``RetryPromptPart`` has no outcome and must not acquire one: the model
    is about to call the same tool again with corrected arguments, which is a
    working loop rather than a failed call."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(
                part=RetryPromptPart(content="fix it", tool_name="t", tool_call_id="call-1")
            )
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert OUTCOME_FIELD not in _extras(result)


async def test_the_events_around_a_failed_result_are_untouched() -> None:
    """Only the result event is stamped.

    A failed return also emits pydantic-ai's own ``REASONING_ENCRYPTED_VALUE``
    continuity claim, which is a different event with a different meaning.
    Holds the ``isinstance`` half of the stamping condition: delete it and this
    event comes back wearing an ``outcome`` it has no field for.
    """
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="failed"))
        )
    )
    others = [e for e in events if not isinstance(e, ToolCallResultEvent)]
    assert any(isinstance(e, ReasoningEncryptedValueEvent) for e in others), (
        "the upstream carrier should still be emitted -- this test is about it "
        "not being stamped, not about it being gone"
    )
    assert all(OUTCOME_FIELD not in _extras(e) for e in others)


async def test_an_output_tool_result_carries_its_outcome() -> None:
    """The structured-output tool emits a ``TOOL_CALL_RESULT`` too, down a
    separate handler, and a client cannot tell which handler produced one."""
    events = await _drain(
        _stream().handle_output_tool_result(
            OutputToolResultEvent(part=_tool_return(outcome="failed"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert _extras(result)[OUTCOME_FIELD] == "failed"


async def test_a_native_tool_return_carries_its_outcome() -> None:
    """A provider-executed tool is the third emitter, and the one whose AG-UI
    ``tool_call_id`` is rewritten -- which is why the outcome is read off the
    part here rather than correlated by id further downstream."""
    stream = _stream()
    # The return handler looks its rewritten id up in a map the call handler
    # fills, so the call has to be announced first.
    await _drain(
        stream.handle_builtin_tool_call_start(
            NativeToolCallPart(tool_name="search", args={}, tool_call_id="call-1")
        )
    )
    events = await _drain(
        stream.handle_builtin_tool_return(
            NativeToolReturnPart(
                tool_name="search", content="c", tool_call_id="call-1", outcome="failed"
            )
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert _extras(result)[OUTCOME_FIELD] == "failed"


async def test_the_outcome_survives_encoding() -> None:
    """The claim the whole change rests on: AG-UI's event models allow extras,
    so the field is not stripped between the model and the wire."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="failed"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert '"outcome":"failed"' in EventEncoder().encode(result)


def test_the_adapter_builds_the_outcome_forwarding_stream() -> None:
    """The stock adapter is what a session used to build, and the substitution
    is the whole wiring -- so it is asserted rather than assumed."""
    adapter = OutcomeAGUIAdapter(Agent(FunctionModel(lambda m, i: ModelResponse(parts=[]))), None)
    assert isinstance(adapter.build_event_stream(), _OutcomeEventStream)


# --- through a whole run ------------------------------------------------------


def _run_input() -> Any:
    payload = {
        "threadId": "t1",
        "runId": "r1",
        "messages": [{"id": "m1", "role": "user", "content": "hi"}],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
    }
    return AGUIAdapter.build_run_input(json.dumps(payload).encode())


def _failing_tool_agent() -> Agent[Any, Any]:
    """An agent whose one tool reports a terminal failure the way a spec toolset
    does -- ``ToolFailed``, which pydantic-ai turns into a tool return marked
    ``failed`` rather than into a dead run.

    ``stream_function``, not ``function``: the session streams, and a
    ``FunctionModel`` given only the non-streaming callable raises on the first
    request rather than answering.
    """

    async def stream_fn(messages: list[Any], info: Any) -> Any:
        returned = any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if returned:
            yield "done"
        else:
            yield {0: DeltaToolCall(name="wobble", json_args="{}", tool_call_id="call-1")}

    agent = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def wobble() -> str:
        """Always refuses."""
        raise ToolFailed("that name is already taken")

    return agent


def _failing_session(**config: Any) -> AgentSession:
    return AgentSession(
        _failing_tool_agent(),
        _run_input(),
        RequestFactory().post("/agent/"),
        deps=AgentDeps(user=None),
        audit_logger=NullAuditLogger(),
        config=build_ag_ui_config(**config),
        conversation_store=NullConversationStore(),
    )


async def test_a_failed_call_reaches_the_browser_marked_failed() -> None:
    """End to end, through the session's whole composed stream.

    Every wrapper the session adds sits downstream of the adapter, so this is
    also the assertion that none of them drops the field on its way out.
    """
    joined = "".join([chunk async for chunk in _failing_session().stream()])

    assert "TOOL_CALL_RESULT" in joined
    assert '"outcome":"failed"' in joined
    # The run survived the failure -- the model answered after reading it, which
    # is what separates a failed *call* from a failed run.
    assert "RUN_ERROR" not in joined


async def test_the_outcome_survives_the_reasoning_filter() -> None:
    """``FORWARD_REASONING = False`` must not cost a client the failure marking.

    It used to. Pydantic-AI's own carrier for a non-success outcome is a
    ``REASONING_ENCRYPTED_VALUE`` event trailing the result -- and this package
    drops every ``REASONING_*`` event under that setting, so the one deployment
    that had opted out of chain-of-thought had also, silently, opted out of
    knowing which of its tool calls failed. Putting the outcome on the result
    event itself is what decouples the two.
    """
    joined = "".join([chunk async for chunk in _failing_session(forward_reasoning=False).stream()])

    assert "REASONING" not in joined, "the filter under test has to actually be on"
    assert '"outcome":"failed"' in joined
