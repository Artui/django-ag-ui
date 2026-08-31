"""``suggestions_activity`` -- follow-up chips over the activity envelope."""

from __future__ import annotations

import pytest
from ag_ui.core import EventType

from django_ag_ui.agent.suggestions_activity import (
    MAX_SUGGESTION_CHARS,
    MAX_SUGGESTIONS,
    SUGGESTIONS_ACTIVITY_TYPE,
    suggestions_activity,
)


def test_it_is_an_ordinary_activity_snapshot() -> None:
    """Vanilla AG-UI: a convention inside an open field, not an extension.

    A client that has never heard of this ``activity_type`` ignores the event,
    which is the whole reason it rides the envelope the protocol already has.
    """
    event = suggestions_activity(["Update the shipping address too"])

    assert event.type is EventType.ACTIVITY_SNAPSHOT
    assert event.activity_type == SUGGESTIONS_ACTIVITY_TYPE
    assert event.content == {"prompts": ["Update the shipping address too"]}


def test_a_repeat_id_replaces_rather_than_stacks() -> None:
    # Load-bearing only on a repeat id, and harmless otherwise: it is what tells
    # the client a set supersedes the row under that id rather than being a
    # second row that happens to share it.
    assert suggestions_activity(["Ask again"]).replace is True


def test_a_generated_id_is_prefixed_out_of_the_message_namespace() -> None:
    """``message_id`` is shared with assistant turns, as the chart one is.

    A set sent under an id an assistant message already used would replace *that
    message* in the transcript.
    """
    assert suggestions_activity(["Ask again"]).message_id.startswith("suggestions-")


def test_a_caller_supplied_id_is_used_verbatim() -> None:
    event = suggestions_activity(["Ask again"], suggestions_id="followups-checkout")

    assert event.message_id == "followups-checkout"


def test_prompts_are_stripped() -> None:
    assert suggestions_activity(["  Ask again \n"]).content == {"prompts": ["Ask again"]}


class TestWhatItRefuses:
    """Raised here rather than trimmed, and that is the point.

    The client draws no more than its own limit and has **no channel** to report
    what it dropped, so a producer working past the cap ships suggestions that
    silently never appear. That is the hole ``chart_limits`` exists to close,
    and it was found there the hard way.
    """

    def test_no_prompts(self) -> None:
        with pytest.raises(ValueError, match="at least one prompt"):
            suggestions_activity([])

    def test_more_than_the_client_will_draw(self) -> None:
        with pytest.raises(ValueError, match=f"at most {MAX_SUGGESTIONS}"):
            suggestions_activity(["a", "b", "c", "d", "e"])

    def test_a_blank_prompt(self) -> None:
        with pytest.raises(ValueError, match="must not be blank"):
            suggestions_activity(["Ask again", "   "])

    def test_a_prompt_too_long_to_be_a_chip(self) -> None:
        with pytest.raises(ValueError, match=str(MAX_SUGGESTION_CHARS)):
            suggestions_activity(["x" * (MAX_SUGGESTION_CHARS + 1)])

    def test_the_cap_itself_is_allowed(self) -> None:
        # The boundary, not just past it: an off-by-one here would refuse the
        # exact number the docs tell a project it may send.
        event = suggestions_activity(["a", "b", "c", "d"])

        assert len(event.content["prompts"]) == MAX_SUGGESTIONS
