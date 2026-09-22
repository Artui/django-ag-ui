from __future__ import annotations

import json
from typing import Any

from ag_ui.core import ToolCallResultEvent
from ag_ui.encoder import EventEncoder

from django_ag_ui.agent.stamp_outcome import OUTCOME_FIELD, stamp_outcome

# The helper a wire-fixture recorder outside this repository imports, so what is
# pinned here is the contract that recorder relies on: the bytes it writes are
# the bytes this server writes. How the stream decides *which* results to stamp
# is ``OutcomeAGUIAdapter``'s, and is tested there.


def _result(**kwargs: Any) -> ToolCallResultEvent:
    return ToolCallResultEvent(message_id="m", tool_call_id="t", content="x", **kwargs)


def _served(event: ToolCallResultEvent) -> dict[str, Any]:
    return json.loads(EventEncoder().encode(event).removeprefix("data: "))


def test_both_carriers_are_written() -> None:
    frame = _served(stamp_outcome(_result(), "failed"))
    assert frame["metadata"] == {OUTCOME_FIELD: "failed"}
    assert frame[OUTCOME_FIELD] == "failed"


def test_other_metadata_keys_survive_the_stamp() -> None:
    stamped = stamp_outcome(_result(metadata={"trace": "t-1"}), "denied")
    assert stamped.metadata == {"trace": "t-1", OUTCOME_FIELD: "denied"}


def test_the_stamp_wins_over_an_outcome_already_in_metadata() -> None:
    # The part's outcome is pydantic-ai's record of how the call went; a stale
    # value under the same key would have a client render the wrong one.
    stamped = stamp_outcome(_result(metadata={OUTCOME_FIELD: "success"}), "failed")
    assert stamped.metadata == {OUTCOME_FIELD: "failed"}


def test_the_event_passed_in_is_left_alone() -> None:
    """``model_copy`` is shallow, so updating the metadata dict in place would
    stamp the caller's own event through the dict the two share."""
    original = _result(metadata={"trace": "t-1"})
    stamp_outcome(original, "failed")
    assert original.metadata == {"trace": "t-1"}
    assert OUTCOME_FIELD not in (original.__pydantic_extra__ or {})
