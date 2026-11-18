from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from django_spec_agents.persistence.types.run_log_entry import RunLogEntry

from django_ag_ui.agent.replay_head import replay_head

_S = {"subagentRunId": "s1"}
_TR2 = "tr2"

# A run whose subagent opens a step, reasons, speaks and calls a tool, around parent work.
SUBAGENT_RUN: list[dict[str, Any]] = [
    {"type": "RUN_STARTED", "threadId": "t1", "runId": "r1"},
    {"type": "STEP_STARTED", "stepName": "plan"},
    {"type": "SUBAGENT_STARTED", "subagentRunId": "s1", "name": "helper"},
    {"type": "STEP_STARTED", "stepName": "plan", **_S},
    {"type": "REASONING_START", "messageId": "rs", **_S},
    {"type": "REASONING_MESSAGE_START", "messageId": "rs", "role": "reasoning", **_S},
    {"type": "REASONING_MESSAGE_CONTENT", "messageId": "rs", "delta": "hmm", **_S},
    {"type": "REASONING_MESSAGE_END", "messageId": "rs", **_S},
    {"type": "REASONING_END", "messageId": "rs", **_S},
    {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant", **_S},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Look", **_S},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "ing", **_S},
    {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "find", **_S},
    {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": '{"q":', **_S},
    {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": ' "x"}', **_S},
    {"type": "TOOL_CALL_END", "toolCallId": "c1", **_S},
    {"type": "TEXT_MESSAGE_END", "messageId": "m1", **_S},
    {"type": "TOOL_CALL_RESULT", "messageId": "tr1", "toolCallId": "c1", "content": "x", **_S},
    {"type": "STEP_FINISHED", "stepName": "plan", **_S},
    {"type": "SUBAGENT_FINISHED", "subagentRunId": "s1"},
    {"type": "SUBAGENT_STARTED", "subagentRunId": "s2", "name": "failing"},
    {"type": "SUBAGENT_ERROR", "subagentRunId": "s2", "message": "no"},
    {"type": "TOOL_CALL_START", "toolCallId": "c2", "toolCallName": "ask", "parentMessageId": "a2"},
    {"type": "TOOL_CALL_ARGS", "toolCallId": "c2", "delta": "{}"},
    {"type": "TOOL_CALL_END", "toolCallId": "c2"},
    {"type": "TOOL_CALL_RESULT", "messageId": _TR2, "toolCallId": "c2", "content": "ok"},
    {"type": "TOOL_CALL_START", "toolCallId": "c3", "toolCallName": "ask", "parentMessageId": _TR2},
    {"type": "TOOL_CALL_END", "toolCallId": "c3"},
    {"type": "STEP_FINISHED", "stepName": "plan"},
    {"type": "RUN_FINISHED", "threadId": "t1", "runId": "r1"},
]


_FIND_SO_FAR = {"name": "find", "arguments": '{"q":'}


def log_of(events: list[dict[str, Any]]) -> list[RunLogEntry]:
    now = datetime.now(UTC)
    return [RunLogEntry(seq=i, event=e, created_at=now) for i, e in enumerate(events, 1)]


def _types(head: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in head]


def test_an_empty_log_has_no_head() -> None:
    assert replay_head([], [], after=0) == ([], 0)


def test_mid_subagent_the_head_reopens_its_subagent_step_and_message_with_their_tags() -> None:
    head, cut = replay_head(log_of(SUBAGENT_RUN), [], after=14)

    assert cut == 14
    assert _types(head) == [
        "RUN_STARTED",
        "MESSAGES_SNAPSHOT",
        "STEP_STARTED",
        "SUBAGENT_STARTED",
        "STEP_STARTED",
        "TEXT_MESSAGE_START",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
    ]
    assert head[-1] == {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": '{"q":', **_S}
    assert head[1]["messages"] == [
        {"id": "rs", "role": "reasoning", "content": "hmm", **_S},
        {"id": "m1", "role": "assistant", "content": "Looking", **_S},
        {
            "id": "c1",
            "role": "assistant",
            "toolCalls": [
                {"id": "c1", "type": "function", "function": {"name": "find", "arguments": '{"q":'}}
            ],
            **_S,
        },
    ]


def test_closed_entities_are_not_reopened_and_a_tool_call_finds_its_parent() -> None:
    head, cut = replay_head(log_of(SUBAGENT_RUN), [], after=28)

    assert cut == 28
    assert _types(head) == ["RUN_STARTED", "MESSAGES_SNAPSHOT", "STEP_STARTED"]
    ids = [(m["id"], [c["id"] for c in m.get("toolCalls", ())]) for m in head[1]["messages"]]
    assert ids == [
        ("rs", []),
        ("m1", []),
        ("c1", ["c1"]),
        ("tr1", []),
        ("a2", ["c2"]),
        ("tr2", []),
        ("c3", ["c3"]),
    ]


def test_a_terminal_event_is_left_to_the_tail_even_past_it() -> None:
    head, cut = replay_head(log_of(SUBAGENT_RUN), [], after=500)

    assert cut == len(SUBAGENT_RUN) - 1
    assert "RUN_FINISHED" not in _types(head)


def test_after_zero_opens_with_the_stored_transcript_less_what_the_run_streams() -> None:
    stored = [{"id": "u1", "role": "user", "content": "hi"}, {"id": "m1", "role": "assistant"}]

    head, cut = replay_head(log_of(SUBAGENT_RUN), stored, after=0)

    assert cut == 1
    assert head[1] == {"type": "MESSAGES_SNAPSHOT", "messages": [stored[0]]}


def test_a_run_started_echoing_its_input_adds_the_messages_the_store_lacks() -> None:
    echo = [
        {"id": "u1", "role": "user", "content": "hi"},
        {"id": "u2", "role": "user", "content": "go"},
    ]
    started = {**SUBAGENT_RUN[0], "input": {"messages": echo}}

    head, _ = replay_head(log_of([started, *SUBAGENT_RUN[1:]]), [echo[0]], after=1)

    assert head[0] is started
    assert head[1]["messages"] == echo


def test_a_tool_call_with_no_arguments_yet_is_reopened_without_an_args_event() -> None:
    head, _ = replay_head(log_of(SUBAGENT_RUN), [], after=13)

    assert head[-1] == SUBAGENT_RUN[12]


def test_a_tool_call_hung_on_an_assistant_message_joins_it() -> None:
    events = [
        SUBAGENT_RUN[0],
        {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
        {
            "type": "TOOL_CALL_START",
            "toolCallId": "c1",
            "toolCallName": "f",
            "parentMessageId": "m1",
        },
    ]

    head, _ = replay_head(log_of(events), [], after=3)

    assert head[1]["messages"] == [
        {
            "id": "m1",
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {"id": "c1", "type": "function", "function": {"name": "f", "arguments": ""}}
            ],
        }
    ]
