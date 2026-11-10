from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ag_ui.core import Message
from django.http import HttpRequest

from django_ag_ui.persistence.types.thread_activity import ThreadActivity


@runtime_checkable
class ThreadActivitySource(Protocol):
    """Where a restored thread's pushed activities come back from."""

    async def activities_for(
        self, thread_id: str, *, messages: Sequence[Message], request: HttpRequest
    ) -> Sequence[ThreadActivity]: ...


__all__ = ["ThreadActivitySource"]
