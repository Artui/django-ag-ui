from __future__ import annotations

from dataclasses import dataclass

from ag_ui.core import ActivitySnapshotEvent


@dataclass(frozen=True)
class ThreadActivity:
    """One pushed activity to put back into a restored thread, and where.

    Returned by a
    [`ThreadActivitySource`][django_ag_ui.ThreadActivitySource]. The ``event``
    is the same object the run pushed --
    [`chart_activity`][django_ag_ui.chart_activity] builds one -- so a project
    re-pushes what it already knows how to build rather than learning a second
    vocabulary for the restore path.

    ``after_message_id`` is the stored message this activity followed, and it is
    the caller's answer to the ordering question the library cannot answer for
    itself: the activity was never in the model's history, so nothing in the
    stored thread records where it belonged. Name the message it came after and
    it lands there; leave it ``None`` -- or name a message this thread does not
    have -- and it lands at the end, which is right for a chart pushed after the
    last turn and wrong for one pushed three turns ago.
    """

    event: ActivitySnapshotEvent
    after_message_id: str | None = None


__all__ = ["ThreadActivity"]
