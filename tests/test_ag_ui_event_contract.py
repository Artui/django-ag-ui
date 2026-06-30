"""Cross-repo AG-UI event-set contract (roadmap CI-1).

The Python (`ag-ui-protocol`) and JS (`@ag-ui/core`) event sets are identical
today, and the trio relies on that — e.g. reasoning rides the ``REASONING_*``
family on both sides (THINK-1). Nothing else in CI would catch them drifting
when either dependency bumps, so this test pins the canonical set: if a bump
adds, removes, or renames an event, this fails and forces a deliberate review.

The **same** ``CANONICAL_AG_UI_EVENTS`` list is asserted in the web component's
suite (`tests/ag_ui_event_contract.test.ts`) and documented in the ecosystem
``architecture.md`` ("Events the trio relies on"). Update all three together.
"""

from __future__ import annotations

from ag_ui.core import EventType

# The 33 AG-UI event types, as of ag-ui-protocol 0.1.18 / @ag-ui/core 0.0.54.
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
        "TEXT_MESSAGE_CHUNK",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TEXT_MESSAGE_START",
        "THINKING_END",
        "THINKING_START",
        "THINKING_TEXT_MESSAGE_CONTENT",
        "THINKING_TEXT_MESSAGE_END",
        "THINKING_TEXT_MESSAGE_START",
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
    # THINK-1 forwards a reasoning model's chain-of-thought on this family; the
    # pinned stack emits REASONING_* (>= 0.1.13, 7 events) and the legacy
    # THINKING_* (5 events) the JS client maps onto it.
    reasoning = {e for e in CANONICAL_AG_UI_EVENTS if e.startswith(("REASONING", "THINKING"))}
    assert len(reasoning) == 12
