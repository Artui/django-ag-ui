from __future__ import annotations

import asyncio
from collections.abc import Callable

from django_spec_agents.persistence.types.run_log_entry import RunLogEntry


class LiveFeed:
    """One live run's logged entries, handed to every reader following it in this process.

    A ``None`` batch says the run ended; a feed seen closed takes no new reader."""

    def __init__(self, loop: asyncio.AbstractEventLoop, *, on_close: Callable[[], object]) -> None:
        self.loop = loop
        self._readers: set[asyncio.Queue[list[RunLogEntry] | None]] = set()
        self._on_close = on_close
        self._closed = False

    def subscribe(self) -> asyncio.Queue[list[RunLogEntry] | None] | None:
        """A queue of every batch logged from now on, or ``None`` once the run has ended."""
        if self._closed:
            return None
        queue: asyncio.Queue[list[RunLogEntry] | None] = asyncio.Queue()
        self._readers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[list[RunLogEntry] | None]) -> None:
        self._readers.discard(queue)

    def publish(self, entries: list[RunLogEntry]) -> None:
        for queue in self._readers:
            queue.put_nowait(entries)

    def close(self) -> None:
        self._closed = True
        self._on_close()
        for queue in self._readers:
            queue.put_nowait(None)
        self._readers.clear()


__all__ = ["LiveFeed"]
