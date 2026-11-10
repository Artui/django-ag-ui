from __future__ import annotations

import time


def event_timestamp() -> int:
    """Milliseconds since the epoch, the unit AG-UI's envelope uses."""
    return int(time.time() * 1000)


__all__ = ["event_timestamp"]
