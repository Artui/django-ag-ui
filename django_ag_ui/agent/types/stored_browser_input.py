from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StoredBrowserInput:
    """A queued browser start's input as sent, the run it resumes, and whether that run parked."""

    body: dict[str, Any]
    resume_from: str | None
    resume_parked: bool = False


__all__ = ["StoredBrowserInput"]
