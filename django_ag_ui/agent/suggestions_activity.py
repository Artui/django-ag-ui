from __future__ import annotations

import uuid
from collections.abc import Sequence

from ag_ui.core import ActivitySnapshotEvent

SUGGESTIONS_ACTIVITY_TYPE = "suggestions"
"""``activity_type`` the client matches on to draw follow-up chips.

A convention inside an extension point the protocol already provides, exactly as
``chart`` is: AG-UI defines the envelope and leaves ``activity_type`` open. A
client that does not know this name ignores the event, which is the graceful
outcome.
"""

MAX_SUGGESTIONS = 4
"""Most prompts one push may carry.

Slack's `assistant.threads.setSuggestedPrompts` caps at four and it is the right
number for the same reason here: the chips wrap onto a second row beyond it, and
a wall of suggestions is a menu rather than a nudge.

**Keep in step with ``MAX_SUGGESTIONS`` in the component's
``src/ui/suggestion_chips.ts``.** The client silently draws no more than its own
limit and has no channel to report the difference, so a producer that does not
know the same number ships suggestions that never appear -- the exact hole
``chart_limits`` exists to close, and it was found there by shipping it.
"""

MAX_SUGGESTION_CHARS = 120
"""Longest one prompt may be.

A suggestion is a question the user might send, not an answer: past a sentence
it stops being scannable, and a chip that wraps to three lines is a paragraph
with a border. Mirrored on the client for the same reason as the count.
"""


def suggestions_activity(
    prompts: Sequence[str], *, suggestions_id: str | None = None
) -> ActivitySnapshotEvent:
    """Offer up to ``MAX_SUGGESTIONS`` follow-up prompts as clickable chips."""
    if not prompts:
        raise ValueError(
            "suggestions_activity() needs at least one prompt; to take the chips "
            "away, send a user message or push a new set."
        )
    if len(prompts) > MAX_SUGGESTIONS:
        raise ValueError(
            f"suggestions_activity() takes at most {MAX_SUGGESTIONS} prompts, got "
            f"{len(prompts)}. The client draws no more than that and cannot report "
            "the ones it dropped."
        )
    for prompt in prompts:
        if not prompt.strip():
            raise ValueError("suggestions_activity() prompts must not be blank.")
        if len(prompt) > MAX_SUGGESTION_CHARS:
            raise ValueError(
                f"suggestions_activity() prompts are capped at {MAX_SUGGESTION_CHARS} "
                f"characters, got {len(prompt)}: {prompt[:40]!r}... A suggestion is a "
                "question the user might send, not an answer."
            )
    return ActivitySnapshotEvent(
        message_id=(
            suggestions_id if suggestions_id is not None else f"suggestions-{uuid.uuid4()}"
        ),
        activity_type=SUGGESTIONS_ACTIVITY_TYPE,
        content={"prompts": [prompt.strip() for prompt in prompts]},
        replace=True,
    )


__all__ = [
    "MAX_SUGGESTIONS",
    "MAX_SUGGESTION_CHARS",
    "SUGGESTIONS_ACTIVITY_TYPE",
    "suggestions_activity",
]
