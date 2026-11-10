from __future__ import annotations

import json
from typing import Any

import pytest
from ag_ui.core import BaseEvent, ReasoningEncryptedValueEvent, ToolCallResultEvent
from ag_ui.encoder import EventEncoder
from django.test import RequestFactory
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from django_spec_agents.policy.audit.null_audit_logger import NullAuditLogger
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai import Agent, ToolFailed
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    OutputToolResultEvent,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.outcome_agui_adapter import (
    OutcomeAGUIAdapter,
    _forwardable_outcome,
    _OutcomeEventStream,
)
from django_ag_ui.agent.stamp_outcome import OUTCOME_FIELD
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory

# --- driving the event stream directly ---------------------------------------
#
# The three handlers under test each turn one Pydantic-AI part into AG-UI
# events, and none of them needs a run to do it. Calling them on a bare
# ``_OutcomeEventStream`` is what makes the native-tool arm reachable at all --
# a provider-executed tool return needs a provider otherwise.


async def _drain(events: Any) -> list[BaseEvent]:
    return [event async for event in events]


def _stream() -> _OutcomeEventStream:
    """A standalone event stream, with no run behind it."""
    return _OutcomeEventStream(None)


def _tool_return(**kwargs: Any) -> ToolReturnPart:
    return ToolReturnPart(tool_name="t", content="c", tool_call_id="call-1", **kwargs)


def _extras(event: BaseEvent) -> dict[str, Any]:
    """The additive fields on ``event``, as a mapping to test membership against."""
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
    """A denial is not a failure, and the client renders it differently."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="denied"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert _extras(result)[OUTCOME_FIELD] == "denied"


async def test_a_successful_call_carries_no_outcome_field() -> None:
    """Absent means success, and that is what makes the field additive."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="success"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert OUTCOME_FIELD not in _extras(result)
    assert OUTCOME_FIELD not in EventEncoder().encode(result)
    assert "metadata" not in EventEncoder().encode(result)


@pytest.mark.filterwarnings("ignore:.*RetryPromptPart.* is deprecated")
def test_a_retry_prompt_is_not_a_failure() -> None:
    """A RetryPromptPart must not acquire an outcome: the model is looping, not failed."""
    part = RetryPromptPart(content="fix it", tool_name="t", tool_call_id="call-1")
    assert _forwardable_outcome(part) is None


async def test_a_retried_call_is_not_a_failure() -> None:
    """From pydantic-ai 2.60 a retry is the call's own part with outcome retried."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="retried"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert OUTCOME_FIELD not in _extras(result)
    assert OUTCOME_FIELD not in EventEncoder().encode(result)


async def test_the_events_around_a_failed_result_are_untouched() -> None:
    """Only the result event is stamped."""
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
    """The structured-output tool emits TOOL_CALL_RESULT down a separate handler."""
    events = await _drain(
        _stream().handle_output_tool_result(
            OutputToolResultEvent(part=_tool_return(outcome="failed"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert _extras(result)[OUTCOME_FIELD] == "failed"


async def test_a_native_tool_return_carries_its_outcome() -> None:
    """A provider-executed tool is a third emitter with its tool_call_id rewritten."""
    stream = _stream()
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
    """AG-UI event models allow extras, so the field is not stripped between model and wire."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="failed"))
        )
    )
    result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    assert '"outcome":"failed"' in EventEncoder().encode(result)


# --- the metadata carrier ------------------------------------------------------
#
# ``@ag-ui/client`` 1.0 strips every key its schemas do not declare, the
# top-level ``outcome`` included, so a 1.0 client only sees the outcome in the
# event's declared ``metadata``. The top-level key stays for 0.x clients. Each
# test here is about one carrier or the pair, and each reads the frame the
# encoder writes where it can, because that is what a client parses.


def _served(event: BaseEvent) -> dict[str, Any]:
    """The event as a client receives it, read off the encoder's own frame."""
    return json.loads(EventEncoder().encode(event).removeprefix("data: "))


async def _failed_result(**kwargs: Any) -> list[ToolCallResultEvent]:
    """Every ``TOOL_CALL_RESULT`` one failed function-tool return produces."""
    events = await _drain(
        _stream().handle_function_tool_result(
            FunctionToolResultEvent(part=_tool_return(outcome="failed", **kwargs))
        )
    )
    return [e for e in events if isinstance(e, ToolCallResultEvent)]


async def test_a_failed_result_carries_its_outcome_in_metadata() -> None:
    """The carrier a 1.0 client keeps."""
    (result,) = await _failed_result()
    assert result.metadata == {OUTCOME_FIELD: "failed"}


async def test_the_encoded_frame_carries_the_outcome_on_both_carriers() -> None:
    """Both keys in the bytes: metadata for 1.0 clients, the top-level key for 0.x clients."""
    (result,) = await _failed_result()
    frame = _served(result)
    assert frame["metadata"] == {"outcome": "failed"}
    assert frame["outcome"] == "failed"


async def test_the_outcome_is_merged_into_metadata_the_event_already_carries() -> None:
    """A tool may return its own AG-UI event, forwarded verbatim and stamped like the result."""
    own = ToolCallResultEvent(
        message_id="own", tool_call_id="call-1", content="c", metadata={"trace": "t-1"}
    )
    results = await _failed_result(metadata=own)
    (stamped,) = [e for e in results if e.message_id == "own"]
    assert _served(stamped)["metadata"] == {"trace": "t-1", "outcome": "failed"}
    assert _served(stamped)["outcome"] == "failed"


def test_the_adapter_builds_the_outcome_forwarding_stream() -> None:
    """The stock adapter is what a session used to build; the substitution is asserted."""
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
    """An agent whose tool reports a terminal failure as ToolFailed, turned into a failed return."""

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
    """End to end, through the session's whole composed stream."""
    joined = "".join([chunk async for chunk in _failing_session().stream()])

    assert "TOOL_CALL_RESULT" in joined
    assert '"outcome":"failed"' in joined
    assert "RUN_ERROR" not in joined


async def test_a_failed_call_reaches_the_browser_with_the_outcome_on_both_carriers() -> None:
    """The same, parsed rather than substring-matched."""
    joined = "".join([chunk async for chunk in _failing_session().stream()])

    (result,) = [
        json.loads(line.removeprefix("data: "))
        for line in joined.splitlines()
        if line.startswith("data: ") and '"TOOL_CALL_RESULT"' in line
    ]
    assert result["metadata"] == {"outcome": "failed"}
    assert result["outcome"] == "failed"


async def test_a_refused_mcp_tool_reaches_the_browser_marked_failed() -> None:
    """The same marking for a tool bridged from ``mcp_server=``."""
    from tests.integrations.spec_mcp_server import server as spec_mcp_server

    view = DjangoAGUIView(ToolRegistry(), model=TestModel(), mcp_server=spec_mcp_server)
    request = AuthedRequestFactory().post("/agent/")
    (bridge,) = view._run_toolsets(request)

    async def stream_fn(messages: list[Any], info: Any) -> Any:
        returned = any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if returned:
            yield "done"
        else:
            args = json.dumps({"a": 1, "b": 2})
            yield {0: DeltaToolCall(name="denied", json_args=args, tool_call_id="call-1")}

    session = AgentSession(
        Agent(FunctionModel(stream_function=stream_fn), toolsets=[bridge]),
        _run_input(),
        request,
        deps=AgentDeps(user=request.user),
        audit_logger=NullAuditLogger(),
        config=build_ag_ui_config(),
        conversation_store=NullConversationStore(),
    )
    joined = "".join([chunk async for chunk in session.stream()])

    (result,) = [
        json.loads(line.removeprefix("data: "))
        for line in joined.splitlines()
        if line.startswith("data: ") and '"TOOL_CALL_RESULT"' in line
    ]
    assert result["outcome"] == "failed"
    assert result["content"] == "denied by policy"
    assert "RUN_ERROR" not in joined


async def test_a_failed_call_emits_a_reasoning_event_on_a_model_that_never_thinks() -> None:
    """The likeliest reason a REASONING_* event reaches a browser, pinned here."""
    joined = "".join([chunk async for chunk in _failing_session().stream()])

    reasoning = [
        json.loads(line.removeprefix("data: "))
        for line in joined.splitlines()
        if line.startswith("data: ") and '"REASONING' in line
    ]

    assert [e["type"] for e in reasoning] == ["REASONING_ENCRYPTED_VALUE"], (
        "a run that cannot think stopped emitting the carrier -- the documented "
        "expectation changes with it"
    )
    assert json.loads(reasoning[0]["encryptedValue"]) == {"pydantic_ai": {"outcome": "failed"}}


async def test_the_outcome_survives_the_reasoning_filter() -> None:
    """``FORWARD_REASONING = False`` must not cost a client the failure marking."""
    joined = "".join([chunk async for chunk in _failing_session(forward_reasoning=False).stream()])

    assert "REASONING" not in joined, "the filter under test has to actually be on"
    assert '"outcome":"failed"' in joined


# --- the dump a stored thread is written from ----------------------------------
#
# ``AgentSession`` persists a run by dumping its messages, and a client replaying
# the thread from the server reads that dump rather than the events it saw. Each
# test dumps pydantic-ai's own types and reads the AG-UI messages that come back,
# by alias, because that is the spelling the thread endpoint serves.


def _dumped_tools(*parts: Any) -> list[dict[str, Any]]:
    """The tool messages ``OutcomeAGUIAdapter.dump_messages`` writes for ``parts``."""
    history: list[Any] = []
    for part in parts:
        history.append(
            ModelResponse(
                parts=[ToolCallPart(tool_name="t", args={}, tool_call_id=part.tool_call_id)]
            )
        )
        history.append(ModelRequest(parts=[part]))
    dumped = OutcomeAGUIAdapter.dump_messages(history)
    return [
        message.model_dump(by_alias=True, exclude_none=True)
        for message in dumped
        if message.role == "tool"
    ]


def test_a_dumped_denied_result_carries_its_outcome_on_both_carriers() -> None:
    (stored,) = _dumped_tools(_tool_return(outcome="denied"))
    assert stored["metadata"] == {OUTCOME_FIELD: "denied"}
    assert stored[OUTCOME_FIELD] == "denied"


def test_upstreams_own_carriers_are_left_in_place() -> None:
    """load_messages reads a stored outcome back, so resumed runs keep it."""
    (stored,) = _dumped_tools(_tool_return(outcome="denied"))
    assert "error" in stored
    assert json.loads(stored["encryptedValue"]) == {"pydantic_ai": {"outcome": "denied"}}


def test_a_dumped_successful_result_carries_no_outcome() -> None:
    (stored,) = _dumped_tools(_tool_return(outcome="success"))
    assert "metadata" not in stored
    assert OUTCOME_FIELD not in stored


def test_a_dumped_retry_prompt_carries_no_outcome() -> None:
    """Upstream writes error on a retry's tool message too, so error cannot stand in."""
    (stored,) = _dumped_tools(
        RetryPromptPart(tool_name="t", content="again", tool_call_id="call-1")
    )
    assert "error" in stored
    assert OUTCOME_FIELD not in stored


def test_a_dumped_retried_call_carries_no_outcome() -> None:
    """The stored copy of a retried call replays as it streamed: unmarked."""
    (stored,) = _dumped_tools(_tool_return(outcome="retried"))
    assert "metadata" not in stored
    assert OUTCOME_FIELD not in stored


def test_each_call_is_stamped_with_its_own_outcome() -> None:
    """A lookup keyed on position or the last outcome seen passes every single-call test."""
    stored = _dumped_tools(
        ToolReturnPart(tool_name="t", content="c", tool_call_id="a", outcome="failed"),
        ToolReturnPart(tool_name="t", content="c", tool_call_id="b"),
        ToolReturnPart(tool_name="t", content="c", tool_call_id="c", outcome="denied"),
    )
    assert [(m["toolCallId"], m.get(OUTCOME_FIELD)) for m in stored] == [
        ("a", "failed"),
        ("b", None),
        ("c", "denied"),
    ]


def test_a_native_tool_return_is_stored_as_upstream_wrote_it() -> None:
    """The documented gap, pinned so closing it is a decision: a native return is not matched."""
    history = [
        ModelResponse(
            parts=[
                NativeToolCallPart(tool_name="search", args={}, tool_call_id="call-1"),
                NativeToolReturnPart(
                    tool_name="search", content="c", tool_call_id="call-1", outcome="failed"
                ),
            ]
        )
    ]
    (stored,) = [m for m in OutcomeAGUIAdapter.dump_messages(history) if m.role == "tool"]
    assert stored.metadata is None


def test_upstreams_keyword_arguments_reach_the_dump() -> None:
    # Passed through rather than restated; below 0.1.11 there is no encryptedValue carrier.
    history = [
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="call-1")]),
        ModelRequest(parts=[_tool_return(outcome="denied")]),
    ]
    dumped = OutcomeAGUIAdapter.dump_messages(history, ag_ui_version="0.1.10")
    (stored,) = [m for m in dumped if m.role == "tool"]
    assert stored.encrypted_value is None
    assert stored.metadata == {OUTCOME_FIELD: "denied"}
