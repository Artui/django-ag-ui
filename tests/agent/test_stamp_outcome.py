from __future__ import annotations

import json
from typing import Any

from ag_ui.core import ToolCallResultEvent, ToolMessage
from ag_ui.encoder import EventEncoder

from django_ag_ui.agent.stamp_outcome import OUTCOME_FIELD, stamp_outcome
from django_ag_ui.persistence.utils import messages_to_jsonable, stored_messages_to_wire

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


# --- the stored tool message ------------------------------------------------------
#
# The thread a client replays from the server holds a ``ToolMessage`` where the
# stream had a result event, and the two have to carry the outcome the same way.
# Read through the persistence serialiser and the thread endpoint's re-dump,
# because those are the bytes a replaying client parses.


def _message(**kwargs: Any) -> ToolMessage:
    return ToolMessage(id="m", role="tool", tool_call_id="t", content="x", **kwargs)


def test_a_stored_tool_message_carries_both_carriers() -> None:
    (stored,) = messages_to_jsonable([stamp_outcome(_message(), "denied")])
    assert stored["metadata"] == {OUTCOME_FIELD: "denied"}
    assert stored[OUTCOME_FIELD] == "denied"


def test_both_carriers_survive_the_thread_endpoints_re_dump() -> None:
    # ``stored_messages_to_wire`` validates and re-dumps every row it serves, and
    # the top-level key is an extra -- the kind of field a strict re-validation
    # would drop on the way out.
    rows = messages_to_jsonable([stamp_outcome(_message(), "denied")])
    (served,) = stored_messages_to_wire(rows)
    assert served["metadata"] == {OUTCOME_FIELD: "denied"}
    assert served[OUTCOME_FIELD] == "denied"


def test_the_message_passed_in_is_left_alone() -> None:
    original = _message(metadata={"trace": "t-1"})
    stamp_outcome(original, "failed")
    assert original.metadata == {"trace": "t-1"}
    assert OUTCOME_FIELD not in (original.__pydantic_extra__ or {})
