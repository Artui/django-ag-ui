"""Cross-repo AG-UI event-set contract.

The trio relies on the Python (`ag-ui-protocol`) and JS (`@ag-ui/core`) event
sets agreeing on every event a producer here emits -- e.g. reasoning rides the
``REASONING_*`` family on both sides. Nothing else in CI would catch them
drifting when either dependency bumps, so this test pins the canonical set: if
a bump adds, removes, or renames an event, this fails and forces a deliberate
review.

A parallel ``CANONICAL_AG_UI_EVENTS`` list is asserted in the web component's
suite (`tests/ag_ui_event_contract.test.ts`) and documented in the ecosystem
``architecture.md``, and the lists are identical: both ends resolve the
protocol's 1.0, which removed the five ``THINKING_*`` events from each SDK's
catalogue. Any difference between them is the review this test exists to
force. Update all three together.
"""

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
    # The stack forwards a reasoning model's chain-of-thought on this family, all
    # seven REASONING_* events of it. The legacy THINKING_* family left the
    # protocol's event set in 1.0, but not the JS client's reach: @ag-ui/client
    # 1.0 still converts an inbound THINKING_* event onto REASONING_*, with a
    # console warning, for a deprecation window. Nothing in the family emits
    # one, so that shim only matters to a producer outside it.
    reasoning = {e for e in CANONICAL_AG_UI_EVENTS if e.startswith("REASONING")}
    assert len(reasoning) == 7
