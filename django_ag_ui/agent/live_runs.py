from __future__ import annotations

import asyncio

from django.http import HttpRequest
from django_spec_agents.persistence.utils import owner_id_for

from django_ag_ui.agent.live_feed import LiveFeed

_Key = tuple[str, str, str]


class LiveRuns:
    """The runs a server owns right now, keyed by owner, so a reattach here follows from memory.

    A run id is unique per owner only, and an anonymous request has no owner to key on."""

    def __init__(self) -> None:
        self._feeds: dict[_Key, LiveFeed] = {}

    def open(self, request: HttpRequest, thread_id: str, run_id: str) -> LiveFeed | None:
        """A feed for a run starting on this loop, registered until it closes."""
        key = _key(request, thread_id, run_id)
        if key is None:
            return None

        feed = LiveFeed(asyncio.get_running_loop(), on_close=lambda: self._feeds.pop(key, None))
        self._feeds[key] = feed
        return feed

    def find(self, request: HttpRequest, thread_id: str, run_id: str) -> LiveFeed | None:
        """The run's feed if it is live here and on this loop; a queue is not for another loop."""
        key = _key(request, thread_id, run_id)
        feed = None if key is None else self._feeds.get(key)
        if feed is None or feed.loop is not asyncio.get_running_loop():
            return None
        return feed


def _key(request: HttpRequest, thread_id: str, run_id: str) -> _Key | None:
    owner = owner_id_for(request)
    return None if owner is None else (owner, thread_id, run_id)


__all__ = ["LiveRuns"]
