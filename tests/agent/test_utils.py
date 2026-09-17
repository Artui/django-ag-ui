"""The shared pump two stream wrappers now race against.

Covered incidentally by both consumers, pinned here on purpose: the lockstep and
the closing of upstream are contracts they rely on rather than details, and a
change that broke either would surface as a flaky delegation or a leaked
provider stream rather than as a failure here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from django_ag_ui.agent.utils import STREAM_END, discard_tasks, pump_in_lockstep


async def _chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


async def test_items_arrive_in_order_and_end_with_the_sentinel() -> None:
    out: asyncio.Queue[object] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(_chunks("a", "b"), out))

    taken: list[object] = []
    while True:
        item = await out.get()
        taken.append(item)
        if item is STREAM_END:
            break
        out.task_done()

    await pump
    assert taken == ["a", "b", STREAM_END]


async def test_upstream_waits_for_task_done_before_producing_again() -> None:
    """The lockstep itself: a consumer that has not acknowledged item N never
    causes item N+1 to be produced. This is the client's backpressure — a browser
    that stops reading has to stop the run."""
    produced: list[str] = []

    async def _counted() -> AsyncIterator[str]:
        for value in ("a", "b"):
            produced.append(value)
            yield value

    out: asyncio.Queue[object] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(_counted(), out))

    assert await out.get() == "a"
    # Give the pump every chance to run ahead. It cannot: it is parked in join().
    for _ in range(10):
        await asyncio.sleep(0)
    assert produced == ["a"]

    out.task_done()
    assert await out.get() == "b"
    assert produced == ["a", "b"]
    out.task_done()
    assert await out.get() is STREAM_END
    await pump


async def test_an_upstream_exception_is_queued_behind_the_items_it_followed() -> None:
    """Raised inside the pump it would surface out of band as a task nobody
    awaited, and out of order with the items the consumer had not taken yet."""

    async def _raises() -> AsyncIterator[str]:
        yield "a"
        raise RuntimeError("upstream blew up")

    out: asyncio.Queue[object] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(_raises(), out))

    assert await out.get() == "a"
    out.task_done()
    queued = await out.get()
    assert isinstance(queued, RuntimeError)
    assert str(queued) == "upstream blew up"
    await pump  # the pump itself ends quietly; the consumer owns the raise


async def test_a_cancelled_pump_still_closes_upstream() -> None:
    """The obligation a pump owes back. A cancellation lands on ``join`` as often
    as inside ``__anext__``, which would otherwise leave the upstream generator
    suspended at its own yield with its ``finally`` unrun."""
    closed = asyncio.Event()

    async def _closeable() -> AsyncIterator[str]:
        try:
            yield "a"
            await asyncio.Event().wait()
        finally:
            closed.set()

    out: asyncio.Queue[object] = asyncio.Queue(maxsize=1)
    pump = asyncio.ensure_future(pump_in_lockstep(_closeable(), out))

    assert await out.get() == "a"
    out.task_done()
    await asyncio.sleep(0)

    await discard_tasks(pump)
    assert closed.is_set()


async def test_discard_tasks_ignores_none_and_waits_for_what_it_cancels() -> None:
    async def _parked() -> None:
        await asyncio.Event().wait()

    task = asyncio.ensure_future(_parked())
    await asyncio.sleep(0)

    await discard_tasks(None, task, None)

    assert task.done()
    with pytest.raises(asyncio.CancelledError):
        task.result()
