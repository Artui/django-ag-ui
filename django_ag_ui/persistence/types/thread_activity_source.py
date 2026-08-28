from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ag_ui.core import Message
from django.http import HttpRequest

from django_ag_ui.persistence.types.thread_activity import ThreadActivity


@runtime_checkable
class ThreadActivitySource(Protocol):
    """Where a restored thread's pushed activities come back from.

    Passed as ``AGUIServer(thread_activity_source=...)``; consulted by
    [`ThreadsView`][django_ag_ui.ThreadsView] on
    ``GET <prefix>threads/<id>/``, whose ``messages`` then carry the returned
    activities alongside the stored turns. Off unless supplied.

    **Why this is a hook and not a setting.** A pushed activity deliberately
    never enters the model's message history -- that is the entire reason to
    push one rather than let the agent call a tool -- and the stored thread *is*
    that history. So the server has nothing to redraw from, and cannot get it
    without keeping a second record beside the conversation, with its own
    ordering, its own identity rules and its own answer for what a resumed run
    does with a snapshot. The project already holds the data (it charted it), so
    the smaller, more honest seam is to ask.

    The stored ``messages`` are handed over so an implementation can work out
    where each activity belongs -- the tool result it accompanied is in there,
    and its id is what
    [`ThreadActivity.after_message_id`][django_ag_ui.ThreadActivity] wants. They
    are the thread as stored; editing them changes nothing, since only the
    returned activities are merged in.

    Async because it runs on the event loop, next to the store's own reads: a
    Django ORM lookup here needs the ``a``-prefixed queryset methods or
    ``sync_to_async``, the same as any other view code in this package.
    """

    async def activities_for(
        self, thread_id: str, *, messages: Sequence[Message], request: HttpRequest
    ) -> Sequence[ThreadActivity]: ...


__all__ = ["ThreadActivitySource"]
