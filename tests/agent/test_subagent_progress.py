"""The sub-agent progress wire contract, asserted key by key.

A separate lane builds the browser half of this against whatever the server
emits, in a different repo, and cannot ask questions -- so what the value looks
like is the deliverable, and these are the assertions that stop it moving by
accident.

The carrier is the choice being pinned. ``ACTIVITY_SNAPSHOT`` is materialised
into a ``role="activity"`` message, persisted with the transcript and replayed on
every thread restore; ``CUSTOM`` is not. Replayed progress is a lie -- a run that
finished last week would redraw "calling search_docs" on every reload -- so
progress takes the carrier a chart does not.
"""

from __future__ import annotations

from ag_ui.core import CustomEvent, EventType

from django_ag_ui import SUBAGENT_EVENT_NAME
from django_ag_ui.agent.subagent_progress import subagent_progress


class TestTheEnvelope:
    def test_it_is_a_custom_event_not_an_activity(self) -> None:
        event = subagent_progress(
            delegation_id="call-1", agent="researcher", phase="started", status="Delegated"
        )

        assert isinstance(event, CustomEvent)
        assert event.type == EventType.CUSTOM

    def test_it_carries_the_agreed_name(self) -> None:
        event = subagent_progress(
            delegation_id="call-1", agent="researcher", phase="started", status="Delegated"
        )

        assert event.name == SUBAGENT_EVENT_NAME == "ag_ui.subagent"

    def test_the_wire_shape_is_what_the_client_reads(self) -> None:
        # Read the payload actually served, by alias, rather than trusting the
        # Python attribute names to match the wire.
        dumped = subagent_progress(
            delegation_id="call-1",
            agent="researcher",
            phase="started",
            status="Delegated to researcher",
        ).model_dump(by_alias=True)

        assert dumped["type"] == "CUSTOM"
        assert dumped["name"] == "ag_ui.subagent"
        assert dumped["value"] == {
            "delegationId": "call-1",
            "agent": "researcher",
            "phase": "started",
            "status": "Delegated to researcher",
        }


class TestTheValue:
    def test_a_non_tool_phase_carries_exactly_four_keys(self) -> None:
        # The collapsed row's whole contract: a client that never expands needs
        # the status line and the delegation it belongs to, and nothing else.
        value = subagent_progress(
            delegation_id="call-1", agent="researcher", phase="finished", status="done"
        ).value

        assert set(value) == {"delegationId", "agent", "phase", "status"}

    def test_the_delegation_id_is_carried_verbatim(self) -> None:
        # It is the parent's own delegate_task tool call id -- the toolCallId the
        # client already drew a card for -- which is what makes this an
        # augmentation of that card rather than a second row beside it.
        value = subagent_progress(
            delegation_id="pyd_ai_9f2c", agent="a", phase="started", status="s"
        ).value

        assert value["delegationId"] == "pyd_ai_9f2c"

    def test_a_tool_call_carries_the_tool_with_an_unknown_outcome(self) -> None:
        value = subagent_progress(
            delegation_id="call-1",
            agent="researcher",
            phase="tool_call",
            status="researcher: calling search_docs",
            tool_call_id="sub-1",
            tool_name="search_docs",
        ).value

        # ``ok`` is present and null rather than absent: a fixed shape is what
        # lets a client create the row here and update it in place on the result.
        assert value["tool"] == {"toolCallId": "sub-1", "name": "search_docs", "ok": None}

    def test_a_tool_result_carries_its_outcome_both_ways(self) -> None:
        def outcome(ok: bool) -> object:
            return subagent_progress(
                delegation_id="call-1",
                agent="researcher",
                phase="tool_result",
                status="s",
                tool_call_id="sub-1",
                tool_name="search_docs",
                ok=ok,
            ).value["tool"]["ok"]

        assert outcome(True) is True
        assert outcome(False) is False

    def test_the_tool_object_is_absent_when_there_is_no_tool(self) -> None:
        value = subagent_progress(
            delegation_id="call-1", agent="researcher", phase="failed", status="failed"
        ).value

        assert "tool" not in value

    def test_a_failure_says_who_and_stops(self) -> None:
        # The channel deliberately carries no exception text: an exception's own
        # words are written for an operator, which is the same reasoning
        # AgentSession applies to RUN_ERROR. The model-facing detail travels the
        # ordinary tool result instead, on the card this progress belongs to.
        value = subagent_progress(
            delegation_id="call-1",
            agent="auditor",
            phase="failed",
            status="auditor failed",
        ).value

        assert value == {
            "delegationId": "call-1",
            "agent": "auditor",
            "phase": "failed",
            "status": "auditor failed",
        }
