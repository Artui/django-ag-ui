from __future__ import annotations

from ag_ui.core import EventType

# The 31 AG-UI event types of ag-ui-protocol 1.0.
#
# Two bumps moved through here without touching the set, and two moved it. The
# 0.1.18 -> 0.1.19 bump (tool-approval interrupt/resume) rides RUN_FINISHED
# *outcomes* + the RunAgentInput.resume field, and 0.1.20 added TokenUsage on
# RUN_FINISHED / RUN_ERROR -- neither is an EventType member. 0.1.21 grew the
# catalogue, adding the three SUBAGENT_* events below. 1.0 shrank it, removing
# the deprecated THINKING_* family (THINKING_START / _END and
# THINKING_TEXT_MESSAGE_START / _CONTENT / _END) in favour of REASONING_*; the
# `cancelled` RUN_FINISHED outcome it added is, like 0.1.19's outcomes, not an
# EventType member.
CANONICAL_AG_UI_EVENTS = frozenset(
    {
        "ACTIVITY_DELTA",
        "ACTIVITY_SNAPSHOT",
        "CUSTOM",
        "MESSAGES_SNAPSHOT",
        "RAW",
        "REASONING_ENCRYPTED_VALUE",
        "REASONING_END",
        "REASONING_MESSAGE_CHUNK",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_MESSAGE_START",
        "REASONING_START",
        "RUN_ERROR",
        "RUN_FINISHED",
        "RUN_STARTED",
        "STATE_DELTA",
        "STATE_SNAPSHOT",
        "STEP_FINISHED",
        "STEP_STARTED",
        "SUBAGENT_ERROR",
        "SUBAGENT_FINISHED",
        "SUBAGENT_STARTED",
        "TEXT_MESSAGE_CHUNK",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TEXT_MESSAGE_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_CHUNK",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TOOL_CALL_START",
    }
)


def test_python_event_set_matches_the_canonical_contract() -> None:
    assert {event.value for event in EventType} == CANONICAL_AG_UI_EVENTS


def test_reasoning_family_is_present() -> None:
    # The stack forwards a reasoning model's chain-of-thought: all seven REASONING_* events.
    reasoning = {e for e in CANONICAL_AG_UI_EVENTS if e.startswith("REASONING")}
    assert len(reasoning) == 7
