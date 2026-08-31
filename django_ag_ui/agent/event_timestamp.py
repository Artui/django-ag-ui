"""``event_timestamp`` -- the wall clock an AG-UI event carries."""

from __future__ import annotations

import time


def event_timestamp() -> int:
    """Milliseconds since the epoch, the unit AG-UI's envelope uses.

    Every event pydantic-ai's adapter emits carries one; the two this package
    builds itself did not, which made ``CUSTOM`` the only type in the stream
    without a wall clock. A consumer reading the raw stream had no way to time a
    delegation, and the asymmetry is the kind a careful reader reports -- which
    is how it was found.

    Deliberately **not** used to compute elapsed time in a browser: a client's
    own clock has no skew against itself, and this one does. It is here so a
    logged stream reads consistently and so nothing has to explain why one event
    type is different.
    """
    return int(time.time() * 1000)


__all__ = ["event_timestamp"]
