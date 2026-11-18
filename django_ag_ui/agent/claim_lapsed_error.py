from __future__ import annotations


class ClaimLapsedError(RuntimeError):
    """A queued run's claim on its thread lapsed before a worker took the run up."""

    def __init__(self, thread_id: str, run_id: str) -> None:
        super().__init__(f"run {run_id!r} lost its claim on thread {thread_id!r} while queued")
        self.thread_id = thread_id
        self.run_id = run_id


__all__ = ["ClaimLapsedError"]
