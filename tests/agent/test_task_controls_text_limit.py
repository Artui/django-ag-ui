from __future__ import annotations

import asyncio

import pytest

from tests.agent.test_task_controls_view import _gated_child, _Run, _said


@pytest.mark.django_db(transaction=True)
async def test_a_steer_of_exactly_8000_characters_is_accepted() -> None:
    gate = asyncio.Event()
    run = _Run(_gated_child(gate, []), _said("Done."))
    await run.start()
    task = await run.task()

    status, body = await run.post(task["task_id"], "steer", {"message": "x" * 8000})
    gate.set()
    await run.steps()

    assert (status, body["steered"]) == (202, True)
