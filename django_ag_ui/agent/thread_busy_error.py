from __future__ import annotations


class ThreadBusyError(RuntimeError):
    """A run was started on a thread another run still holds; ``run_id`` names that run."""

    def __init__(self, thread_id: str, run_id: str) -> None:
        super().__init__(f"thread {thread_id!r} has a run in progress: {run_id!r}")
        self.thread_id = thread_id
        self.run_id = run_id


__all__ = ["ThreadBusyError"]
