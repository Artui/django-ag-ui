from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from django_ag_ui.agent.clear_tool_results import _CLEARED, ClearToolResults, _clear_stale
from django_ag_ui.agent.sliding_window_compaction import SlidingWindowCompaction, _window


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_exchange(name: str, result: str) -> tuple[ModelResponse, ModelRequest]:
    call = ModelResponse(parts=[ToolCallPart(tool_name=name, args={}, tool_call_id=name)])
    ret = ModelRequest(parts=[ToolReturnPart(tool_name=name, content=result, tool_call_id=name)])
    return call, ret


# --- SlidingWindowCompaction ---------------------------------------------------


def test_short_history_passes_through_unchanged() -> None:
    messages = [_user("hi"), _assistant("hello")]
    assert _window(messages, 10) is messages


def test_window_keeps_opener_and_newest_messages() -> None:
    messages = [_user("opener")] + [
        message for i in range(6) for message in (_user(f"q{i}"), _assistant(f"a{i}"))
    ]
    windowed = _window(messages, 4)
    assert windowed[0].parts[0].content == "opener"
    assert len(windowed) <= 4
    assert windowed[-1].parts[0].content == "a5"


def test_cut_never_starts_the_window_on_a_tool_return() -> None:
    call, ret = _tool_exchange("lookup", "found")
    messages = [
        _user("opener"),
        _user("q1"),
        _assistant("a1"),
        call,
        ret,  # a naive cut of the last 3 would start here — orphaning the call
        _assistant("a2"),
    ]
    windowed = _window(messages, 3)
    assert windowed[0].parts[0].content == "opener"
    # The cut advanced past the response/tool-return pair to a clean start...
    assert not any(
        isinstance(part, ToolReturnPart | RetryPromptPart)
        for part in windowed[1].parts
        if isinstance(windowed[1], ModelRequest)
    )
    # ...which in this history means only non-request messages remain after the
    # opener until the next clean request — the assistant tail.
    assert windowed[-1].parts[0].content == "a2"


def test_max_messages_must_leave_room_for_a_window() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        SlidingWindowCompaction(max_messages=1)


async def test_capability_rewrites_the_request_context() -> None:
    from types import SimpleNamespace

    messages = [_user("opener")] + [
        message for i in range(9) for message in (_user(f"q{i}"), _assistant(f"a{i}"))
    ]
    context = SimpleNamespace(messages=messages)
    result = await SlidingWindowCompaction(max_messages=5).before_model_request(None, context)
    assert len(result.messages) <= 5
    assert result.messages[0].parts[0].content == "opener"


# --- ClearToolResults -----------------------------------------------------------


def test_newest_tool_results_are_kept_older_are_cleared() -> None:
    exchanges = [_tool_exchange(f"t{i}", f"result-{i}") for i in range(4)]
    messages = [_user("go")] + [message for pair in exchanges for message in pair]
    cleared = _clear_stale(messages, keep_last=2)
    returns = [
        part
        for message in cleared
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [part.content for part in returns] == [_CLEARED, _CLEARED, "result-2", "result-3"]
    # Structure preserved: same number of messages, calls untouched.
    assert len(cleared) == len(messages)


def test_under_the_keep_budget_nothing_changes() -> None:
    call, ret = _tool_exchange("t", "r")
    messages = [_user("go"), call, ret]
    assert _clear_stale(messages, keep_last=3) is messages


def test_negative_keep_last_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ClearToolResults(keep_last=-1)


async def test_clear_capability_rewrites_the_request_context() -> None:
    from types import SimpleNamespace

    exchanges = [_tool_exchange(f"t{i}", f"result-{i}") for i in range(3)]
    messages = [_user("go")] + [message for pair in exchanges for message in pair]
    context = SimpleNamespace(messages=messages)
    result = await ClearToolResults(keep_last=1).before_model_request(None, context)
    returns = [
        part
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [part.content for part in returns] == [_CLEARED, _CLEARED, "result-2"]
