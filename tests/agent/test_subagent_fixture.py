from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_subagent_fixture.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "subagent_progress_stream.json"


def _document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def _progress() -> list[dict[str, Any]]:
    """The ``CUSTOM`` steps: one per tool call the child made."""
    return [
        event["value"]
        for event in _document()["events"]
        if event["type"] == "CUSTOM" and event.get("name") == "ag_ui.subagent"
    ]


def _lifecycle() -> list[dict[str, Any]]:
    """The protocol's own three, which open and close each delegation."""
    return [event for event in _document()["events"] if str(event["type"]).startswith("SUBAGENT_")]


def test_regenerating_the_fixture_changes_nothing(tmp_path: pathlib.Path) -> None:
    regenerated = tmp_path / "regenerated.json"
    # The generator configures its own settings; the fixture describes the shipped endpoint.
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}

    subprocess.run(
        [sys.executable, str(GENERATOR), str(regenerated)],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
    )

    assert regenerated.read_bytes() == FIXTURE.read_bytes()


def test_the_fixture_covers_every_phase_of_the_contract() -> None:
    # What the browser lane is entitled to find in it.
    assert {event["type"] for event in _lifecycle()} == {
        "SUBAGENT_STARTED",
        "SUBAGENT_FINISHED",
        "SUBAGENT_ERROR",
    }
    assert {value["phase"] for value in _progress()} == {"tool_call", "tool_result"}


def test_the_fixture_shows_both_tool_outcomes() -> None:
    outcomes = {value["tool"]["ok"] for value in _progress() if "tool" in value}

    assert outcomes == {None, True, False}


def test_every_delegation_opens_once_and_closes_once() -> None:
    # @ag-ui/client verifies this: a reused subagentRunId, or one open at RUN_FINISHED, is refused.
    seen: dict[str, list[str]] = {}
    for event in _lifecycle():
        seen.setdefault(event["subagentRunId"], []).append(event["type"])

    for types in seen.values():
        assert types[0] == "SUBAGENT_STARTED"
        assert types[-1] in {"SUBAGENT_FINISHED", "SUBAGENT_ERROR"}
        assert len(types) == 2


def test_a_delegation_is_keyed_to_a_tool_call_the_client_already_saw() -> None:
    # Both carriers, as a client joins them by this id; each names a card drawn on TOOL_CALL_START.
    drawn = {
        event["toolCallId"] for event in _document()["events"] if event["type"] == "TOOL_CALL_START"
    }
    announced = {value["delegationId"] for value in _progress()}
    linked = {
        event["parentToolCallId"] for event in _lifecycle() if event["type"] == "SUBAGENT_STARTED"
    }

    assert announced <= drawn
    assert linked <= drawn


def test_every_tool_call_names_a_message_the_stream_announced() -> None:
    # A response starting with a tool call has no text to open its assistant message with.
    announced = {
        event["messageId"]
        for event in _document()["events"]
        if event["type"] == "TEXT_MESSAGE_START"
    }
    parents = {
        event["parentMessageId"]
        for event in _document()["events"]
        if event["type"] == "TOOL_CALL_START" and event.get("parentMessageId") is not None
    }

    assert parents, "the scenario has to contain a parented tool call for this to mean anything"
    assert parents <= announced


def test_the_failure_detail_rides_the_tool_result_and_not_the_progress() -> None:
    # The split the contract turns on.
    failed = [event for event in _lifecycle() if event["type"] == "SUBAGENT_ERROR"]
    results = [
        event["content"] for event in _document()["events"] if event["type"] == "TOOL_CALL_RESULT"
    ]

    assert [event["message"] for event in failed] == ["auditor failed"]
    assert all("code" not in event for event in failed)
    assert any("the auditor model went away" in content for content in results)
