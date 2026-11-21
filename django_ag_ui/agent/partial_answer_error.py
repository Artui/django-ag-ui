from __future__ import annotations


class PartialAnswerError(ValueError):
    """An answer to a parked thread left calls open; ``unanswered`` names them, the park stands."""

    def __init__(self, thread_id: str, unanswered: tuple[str, ...]) -> None:
        open_calls = ", ".join(unanswered)
        super().__init__(f"thread {thread_id!r} is parked on calls left unanswered: {open_calls}")
        self.thread_id = thread_id
        self.unanswered = unanswered


__all__ = ["PartialAnswerError"]
