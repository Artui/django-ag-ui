from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubAgentDelegation:
    """The delegation a child run belongs to, as the client keys it: the parent's call id."""

    delegation_id: str
    agent: str


__all__ = ["SubAgentDelegation"]
