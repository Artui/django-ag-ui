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
        # Read the payload served, by alias, not the Python attribute names.
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
        # The whole contract.
        value = _step(phase="tool_call").value

        assert set(value) == {"delegationId", "agent", "phase", "status", "tool"}

    def test_the_delegation_id_is_carried_verbatim(self) -> None:
        # The parent's delegate_task call id, the one the client already drew a card for.
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

        # ok is present and null, not absent, so the client creates the row and updates it in place.
        assert value["tool"] == {"toolCallId": "sub-1", "name": "search_docs", "ok": None}

    def test_a_tool_result_carries_its_outcome_both_ways(self) -> None:
        def outcome(ok: bool) -> object:
            return _step(phase="tool_result", ok=ok).value["tool"]["ok"]

        assert outcome(True) is True
        assert outcome(False) is False

    def test_the_null_outcome_survives_serialisation(self) -> None:
        # Not a restatement of the builder test above.
        dumped = _step(phase="tool_call").model_dump(by_alias=True)

        assert "ok" in dumped["value"]["tool"]
        assert dumped["value"]["tool"]["ok"] is None
