from __future__ import annotations

from typing import Literal

TaskControlReason = Literal[
    "invalid", "no_run", "not_live_here", "no_task", "not_running", "not_waiting", "remote"
]


class TaskControlRefused(Exception):
    """A task control the run could not take: ``status`` and ``reason`` are the response's."""

    def __init__(self, status: int, reason: TaskControlReason, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.reason = reason
        self.error = error


__all__ = ["TaskControlReason", "TaskControlRefused"]
