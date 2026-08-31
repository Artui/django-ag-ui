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

That is also why only *these two* phases are here. The delegation's own lifetime
moved to the protocol's ``SUBAGENT_*`` events, which have the same ephemerality;
the child's individual tool calls could not follow, because the protocol would
have them be ordinary ``TOOL_CALL_*`` events tagged with ``subagentRunId``, and
those are materialised and replayed. See ``test_subagent_lifecycle``.
"""

from __future__ import annotations

from typing import Literal

from ag_ui.core import CustomEvent, EventType

from django_ag_ui import SUBAGENT_EVENT_NAME
from django_ag_ui.agent.subagent_progress import subagent_progress


def _step(
    *,
    phase: Literal["tool_call", "tool_result"],
    ok: bool | None = None,
) -> CustomEvent:
    """One step event with the module's standing example filled in."""
    return subagent_progress(
        delegation_id="call-1",
        agent="researcher",
        phase=phase,
        status="researcher: calling search_docs",
        tool_call_id="sub-1",
        tool_name="search_docs",
        ok=ok,
    )


class TestTheEnvelope:
    def test_it_is_a_custom_event_not_an_activity(self) -> None:
        event = _step(phase="tool_call")

        assert isinstance(event, CustomEvent)
        assert event.type == EventType.CUSTOM

    def test_it_carries_the_agreed_name(self) -> None:
        event = _step(phase="tool_call")

        assert event.name == SUBAGENT_EVENT_NAME == "ag_ui.subagent"

    def test_the_wire_shape_is_what_the_client_reads(self) -> None:
        # Read the payload actually served, by alias, rather than trusting the
        # Python attribute names to match the wire.
        dumped = _step(phase="tool_call").model_dump(by_alias=True)

        assert dumped["type"] == "CUSTOM"
        assert dumped["name"] == "ag_ui.subagent"
        assert dumped["value"] == {
            "delegationId": "call-1",
            "agent": "researcher",
            "phase": "tool_call",
            "status": "researcher: calling search_docs",
            "tool": {"toolCallId": "sub-1", "name": "search_docs", "ok": None},
        }


class TestTheValue:
    def test_every_step_carries_exactly_five_keys(self) -> None:
        # The whole contract. A client that never expands a row reads the status
        # line and the delegation it belongs to; one that does reads the tool.
        value = _step(phase="tool_call").value

        assert set(value) == {"delegationId", "agent", "phase", "status", "tool"}

    def test_the_delegation_id_is_carried_verbatim(self) -> None:
        # It is the parent's own delegate_task tool call id -- the toolCallId the
        # client already drew a card for -- which is what makes this an
        # augmentation of that card rather than a second row beside it.
        value = subagent_progress(
            delegation_id="pyd_ai_9f2c",
            agent="a",
            phase="tool_call",
            status="s",
            tool_call_id="sub-1",
            tool_name="search_docs",
        ).value

        assert value["delegationId"] == "pyd_ai_9f2c"

    def test_a_tool_call_carries_the_tool_with_an_unknown_outcome(self) -> None:
        value = _step(phase="tool_call").value

        # ``ok`` is present and null rather than absent: a fixed shape is what
        # lets a client create the row here and update it in place on the result.
        assert value["tool"] == {"toolCallId": "sub-1", "name": "search_docs", "ok": None}

    def test_a_tool_result_carries_its_outcome_both_ways(self) -> None:
        def outcome(ok: bool) -> object:
            return _step(phase="tool_result", ok=ok).value["tool"]["ok"]

        assert outcome(True) is True
        assert outcome(False) is False

    def test_the_null_outcome_survives_serialisation(self) -> None:
        # Not a restatement of the builder test above. From 0.1.21 the protocol
        # omits an unset optional field rather than writing it as null, and the
        # browser lane refuses a tool record whose ``ok`` is missing -- so what
        # matters is that this null is still on the wire after a dump, which it
        # is because it lives in a plain dict rather than in a model field.
        dumped = _step(phase="tool_call").model_dump(by_alias=True)

        assert "ok" in dumped["value"]["tool"]
        assert dumped["value"]["tool"]["ok"] is None
