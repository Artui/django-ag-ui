"""``CompactionObserver`` — make a compaction capability's work visible to the client."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import WrapperCapability

# The per-run sink a wrapped capability appends to, drained by
# ``inject_compaction_events`` while it forwards the AG-UI stream.
#
# A ``ContextVar`` rather than state on the observer, which is built once at
# configuration time and then serves every request: an instance-level list would
# interleave concurrent runs into each other's transcripts. Context variables are
# per-task, and a run whose stream never set one records nothing.
COMPACTION_SINK: ContextVar[list[Compaction] | None] = ContextVar(
    "django_ag_ui_compaction_sink", default=None
)


@dataclass(frozen=True)
class Compaction:
    """One firing of a compaction strategy: how many messages it removed."""

    before: int
    after: int

    @property
    def removed(self) -> int:
        return self.before - self.after


class CompactionObserver(WrapperCapability[Any]):
    """Wraps a compaction capability and records each time it trims the history.

    Compaction is deliberately invisible upstream — it happens inside
    ``before_model_request`` and emits nothing — so a user watching a long run
    sees earlier turns silently vanish from the model's view with no explanation.
    Wrapping the capability is the seam for saying so:

        capabilities=[CompactionObserver(SlidingWindowCompaction(max_messages=80))]

    Opt-in by construction: passing the strategy unwrapped emits nothing.

    Subclassing ``WrapperCapability`` (pydantic-ai's supported wrapper, analogous
    to ``WrapperToolset``) rather than hand-rolling a proxy keeps the rest of the
    capability protocol intact — ordering, the ``has_*`` hook-introspection flags
    and every other lifecycle method delegate untouched.

    Detection is a message-count comparison across the delegated call, because
    that is all the upstream contract exposes. A strategy that rewrites history
    without shortening it registers nothing, which is the honest limit of this
    seam and matches what the indicator claims: turns were dropped.
    """

    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped)

    async def before_model_request(self, ctx: Any, request_context: Any) -> Any:
        before = len(request_context.messages)
        result = await super().before_model_request(ctx, request_context)
        after = len(result.messages)
        sink = COMPACTION_SINK.get()
        if sink is not None and after < before:
            sink.append(Compaction(before=before, after=after))
        return result


__all__ = ["COMPACTION_SINK", "Compaction", "CompactionObserver"]
