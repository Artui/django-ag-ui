from __future__ import annotations

from dataclasses import dataclass

from ag_ui.core import ActivitySnapshotEvent


@dataclass(frozen=True)
class ThreadActivity:
    """One pushed activity to put back into a restored thread, and where."""

    event: ActivitySnapshotEvent
    after_message_id: str | None = None


__all__ = ["ThreadActivity"]
