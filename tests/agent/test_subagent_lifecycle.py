from __future__ import annotations

from ag_ui.core import (
    EventType,
    SubagentErrorEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
)

from django_ag_ui.agent.subagent_lifecycle import subagent_lifecycle, subagent_run_id


class TestTheRunId:
    def test_it_is_derived_from_the_delegation_rather_than_minted(self) -> None:
        # Determinism keeps the wire fixture byte-stable; a tool call id is already unique.
        assert subagent_run_id("call-1") == subagent_run_id("call-1")

    def test_it_is_distinct_from_the_delegation_it_names(self) -> None:
        # Two different things in two different protocol fields.
        assert subagent_run_id("call-1") != "call-1"

    def test_two_delegations_never_collide(self) -> None:
        # The protocol refuses a reused subagentRunId in one run, so a collision is rejected.
        assert subagent_run_id("call-1") != subagent_run_id("call-2")

    def test_the_opening_and_the_close_name_the_same_run(self) -> None:
        # What makes a close a close.
        opened = subagent_lifecycle(delegation_id="call-1", agent="researcher", phase="started")
        closed = subagent_lifecycle(delegation_id="call-1", agent="researcher", phase="finished")

        assert opened.subagent_run_id == closed.subagent_run_id


class TestTheOpeningEvent:
    def test_it_is_the_protocols_own_event(self) -> None:
        event = subagent_lifecycle(delegation_id="call-1", agent="researcher", phase="started")

        assert isinstance(event, SubagentStartedEvent)
        assert event.type == EventType.SUBAGENT_STARTED

    def test_the_wire_shape_is_what_the_client_reads(self) -> None:
        # Read the payload served, by alias, not the Python attribute names.
        dumped = subagent_lifecycle(
            delegation_id="call-1", agent="researcher", phase="started"
        ).model_dump(by_alias=True)

        assert dumped["type"] == "SUBAGENT_STARTED"
        assert dumped["subagentRunId"] == "subagent-call-1"
        assert dumped["name"] == "researcher"
        assert dumped["parentToolCallId"] == "call-1"

    def test_the_delegation_travels_as_the_protocols_parent_tool_call(self) -> None:
        # The field the protocol provides for agents-as-tools: the toolCallId from TOOL_CALL_START.
        event = subagent_lifecycle(delegation_id="pyd_ai_9f2c", agent="a", phase="started")

        assert event.parent_tool_call_id == "pyd_ai_9f2c"


class TestClosingTheDelegation:
    def test_a_finish_is_the_protocols_own_event(self) -> None:
        event = subagent_lifecycle(delegation_id="call-1", agent="researcher", phase="finished")

        assert isinstance(event, SubagentFinishedEvent)
        assert event.type == EventType.SUBAGENT_FINISHED

    def test_a_failure_is_an_error_event_rather_than_a_finish(self) -> None:
        event = subagent_lifecycle(delegation_id="call-1", agent="auditor", phase="failed")

        assert isinstance(event, SubagentErrorEvent)
        assert event.type == EventType.SUBAGENT_ERROR

    def test_a_failure_says_who_and_stops(self) -> None:
        # The protocol requires the message field; the question is what it may say.
        event = subagent_lifecycle(delegation_id="call-1", agent="auditor", phase="failed")

        assert event.message == "auditor failed"
        assert event.code is None


class TestWhatIsOmittedRatherThanNulled:
    """The half of the contract that is about absence, and it is load-bearing."""

    def test_an_opening_omits_the_three_links_it_does_not_have(self) -> None:
        dumped = subagent_lifecycle(
            delegation_id="call-1", agent="researcher", phase="started"
        ).model_dump(by_alias=True)

        for key in ("description", "parentSubagentRunId", "parentMessageId"):
            assert key not in dumped

    def test_a_finish_omits_the_outcome_and_result_it_does_not_carry(self) -> None:
        dumped = subagent_lifecycle(
            delegation_id="call-1", agent="researcher", phase="finished"
        ).model_dump(by_alias=True)

        for key in ("outcome", "result"):
            assert key not in dumped

    def test_a_failure_omits_the_code_it_does_not_set(self) -> None:
        dumped = subagent_lifecycle(
            delegation_id="call-1", agent="auditor", phase="failed"
        ).model_dump(by_alias=True)

        assert "code" not in dumped
