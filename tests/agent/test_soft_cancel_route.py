from __future__ import annotations

import asyncio

import pytest

from tests.agent.test_task_controls_view import _gated_child, _Run, _said


@pytest.mark.django_db(transaction=True)
async def test_a_soft_cancel_stops_the_task_before_its_next_model_request() -> None:
    gate, seen = asyncio.Event(), []
    run = _Run(_gated_child(gate, seen), _said("Stopped."))
    await run.start()
    task = await run.task()

    status, body = await run.post(task["task_id"], "cancel", {"force": False})
    gate.set()
    steps = await run.steps()

    assert (status, body["cancel_requested"], body["forced"]) == (202, True, False)
    asked = [s["value"] for s in steps if s.get("value", {}).get("phase") == "cancel_requested"]
    assert [a["task"] for a in asked] == [
        {"taskId": task["task_id"], "forced": False, "source": "user"}
    ]
    assert [s["code"] for s in steps if "code" in s] == ["cancelled"]
    assert seen == []
    assert (await run.post(task["task_id"], "steer", {"message": "x"}))[1]["reason"] == "no_run"
