from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StoredBrowserInput:
    """A queued browser start's input as its client sent it, and the run it resumes."""

    body: dict[str, Any]
    resume_from: str | None


__all__ = ["StoredBrowserInput"]
