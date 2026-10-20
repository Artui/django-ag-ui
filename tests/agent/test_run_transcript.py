from __future__ import annotations

from typing import Any

from ag_ui.core import (
    AssistantMessage,
    FunctionCall,
    ReasoningEndEvent,
    ReasoningMessage,
    ReasoningMessageContentEvent,
    ReasoningStartEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCall,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    ToolMessage,
    UserMessage,
)

from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.agent.stamp_outcome import OUTCOME_FIELD, stamp_outcome


def test_empty_transcript_yields_no_messages() -> None:
    assert RunTranscript().messages() == []


def test_completed_text_message_is_reconstructed() -> None:
    transcript = RunTranscript()
    transcript.add(TextMessageStartEvent(message_id="m1", role="assistant"))
    transcript.add(TextMessageContentEvent(message_id="m1", delta="Hello "))
    transcript.add(TextMessageContentEvent(message_id="m1", delta="world"))
    transcript.add(TextMessageEndEvent(message_id="m1"))

    assert transcript.messages() == [
        AssistantMessage(id="m1", role="assistant", content="Hello world"),
    ]


def test_open_text_buffer_is_included_as_partial_text() -> None:
    # The cancel case: deltas streamed, no TEXT_MESSAGE_END.
    transcript = RunTranscript()
    transcript.add(TextMessageStartEvent(message_id="m1", role="assistant"))
    transcript.add(TextMessageContentEvent(message_id="m1", delta="partial ans"))

    (message,) = transcript.messages()
    assert message.content == "partial ans"


def test_started_but_empty_message_is_skipped() -> None:
    transcript = RunTranscript()
    transcript.add(TextMessageStartEvent(message_id="m1", role="assistant"))
    assert transcript.messages() == []


def test_completed_tool_call_and_result_are_reconstructed_in_order() -> None:
    transcript = RunTranscript()
    transcript.add(TextMessageStartEvent(message_id="m1", role="assistant"))
    transcript.add(TextMessageContentEvent(message_id="m1", delta="Doubling."))
    transcript.add(
        ToolCallStartEvent(tool_call_id="c1", tool_call_name="double", parent_message_id="m1"),
    )
    transcript.add(ToolCallArgsEvent(tool_call_id="c1", delta='{"n":'))
    transcript.add(ToolCallArgsEvent(tool_call_id="c1", delta=" 5}"))
    transcript.add(ToolCallEndEvent(tool_call_id="c1"))
    transcript.add(ToolCallResultEvent(message_id="t1", tool_call_id="c1", content="10"))

    assistant, tool = transcript.messages()
    assert isinstance(assistant, AssistantMessage)
    assert assistant.content == "Doubling."
    assert assistant.tool_calls is not None
    (call,) = assistant.tool_calls
    assert call.id == "c1"
    assert call.function.name == "double"
    assert call.function.arguments == '{"n": 5}'
    assert isinstance(tool, ToolMessage)
    assert tool.tool_call_id == "c1"
    assert tool.content == "10"


def test_tool_call_without_parent_message_gets_its_own_message() -> None:
    transcript = RunTranscript()
    transcript.add(ToolCallStartEvent(tool_call_id="c1", tool_call_name="double"))
    transcript.add(ToolCallArgsEvent(tool_call_id="c1", delta="{}"))
    transcript.add(ToolCallEndEvent(tool_call_id="c1"))

    (message,) = transcript.messages()
    assert isinstance(message, AssistantMessage)
    assert message.content is None
    assert message.tool_calls is not None and message.tool_calls[0].id == "c1"


def test_partially_streamed_tool_call_is_dropped() -> None:
    # No TOOL_CALL_END — half a JSON arguments string is not a usable record.
    transcript = RunTranscript()
    transcript.add(ToolCallStartEvent(tool_call_id="c1", tool_call_name="double"))
    transcript.add(ToolCallArgsEvent(tool_call_id="c1", delta='{"n":'))

    assert transcript.messages() == []


def test_unrelated_events_are_ignored() -> None:
    transcript = RunTranscript()
    transcript.add(RunStartedEvent(thread_id="t1", run_id="r1"))
    assert transcript.messages() == []


def test_two_text_messages_stay_separate_and_ordered() -> None:
    transcript = RunTranscript()
    transcript.add(TextMessageStartEvent(message_id="m1", role="assistant"))
    transcript.add(TextMessageContentEvent(message_id="m1", delta="first"))
    transcript.add(TextMessageEndEvent(message_id="m1"))
    transcript.add(TextMessageStartEvent(message_id="m2", role="assistant"))
    transcript.add(TextMessageContentEvent(message_id="m2", delta="second"))

    first, second = transcript.messages()
    assert (first.id, first.content) == ("m1", "first")
    assert (second.id, second.content) == ("m2", "second")


async def test_observe_passes_events_through_unchanged_while_recording() -> None:
    transcript = RunTranscript()
    events = [
        TextMessageStartEvent(message_id="m1", role="assistant"),
        TextMessageContentEvent(message_id="m1", delta="hi"),
    ]

    async def _stream():  # noqa: ANN202
        for event in events:
            yield event

    seen = [event async for event in transcript.observe(_stream())]
    assert seen == events
    (message,) = transcript.messages()
    assert message.content == "hi"


def test_reasoning_events_are_tolerated_and_not_persisted() -> None:
    # Reasoning is ephemeral: the transcript must ignore THINKING_*/REASONING_*
    # events (no crash, nothing persisted) so a cancelled run reconstructs only
    # the durable text/tool messages.
    transcript = RunTranscript()
    transcript.add(ReasoningStartEvent(message_id="r1"))
    transcript.add(ReasoningMessageContentEvent(message_id="r1", delta="hmm…"))
    transcript.add(ReasoningEndEvent(message_id="r1"))
    transcript.add(TextMessageStartEvent(message_id="m1", role="assistant"))
    transcript.add(TextMessageContentEvent(message_id="m1", delta="answer"))

    (message,) = transcript.messages()
    assert (message.id, message.content) == ("m1", "answer")


# --- the outcome a result streamed with -------------------------------------------
#
# A failed run is persisted from this transcript rather than from a dump, so the
# outcome the stream stamped has to survive the rebuild or the call is stored as
# a success.


def _result(**kwargs: object) -> ToolCallResultEvent:
    return ToolCallResultEvent(message_id="t1", tool_call_id="c1", content="no", **kwargs)


def test_a_stamped_result_is_recorded_with_its_outcome() -> None:
    transcript = RunTranscript()
    transcript.add(stamp_outcome(_result(), "failed"))

    (tool,) = transcript.messages()
    assert tool.metadata == {OUTCOME_FIELD: "failed"}
    assert (tool.__pydantic_extra__ or {})[OUTCOME_FIELD] == "failed"


def test_an_unstamped_result_is_recorded_with_no_outcome() -> None:
    # Absent means success, and ``metadata`` stays absent rather than empty.
    transcript = RunTranscript()
    transcript.add(_result())

    (tool,) = transcript.messages()
    assert tool.metadata is None
    assert OUTCOME_FIELD not in (tool.__pydantic_extra__ or {})


def test_only_the_outcome_is_carried_over_from_the_event() -> None:
    # The dump that stores a completed run has no event metadata to copy, so a
    # transcript that copied the rest would store a thread differently depending
    # on how its run ended.
    transcript = RunTranscript()
    transcript.add(stamp_outcome(_result(metadata={"trace": "t-1"}), "denied"))

    (tool,) = transcript.messages()
    assert tool.metadata == {OUTCOME_FIELD: "denied"}


# --- lending a completed run's dump the ids the stream announced -------------------
#
# The shapes the session tests cannot reach through a ``FunctionModel``: each is
# a dump the stream does not line up with one-for-one, where the right answer is
# the dumped id rather than a neighbour's streamed one.


def _call(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, type="function", function=FunctionCall(name="f", arguments="{}"))


def _calls_message(message_id: str, *call_ids: str) -> AssistantMessage:
    return AssistantMessage(
        id=message_id, role="assistant", tool_calls=[_call(c) for c in call_ids]
    )


def _streamed_call(transcript: RunTranscript, call_id: str, parent: str | None) -> None:
    transcript.add(
        ToolCallStartEvent(tool_call_id=call_id, tool_call_name="f", parent_message_id=parent)
    )
    transcript.add(ToolCallEndEvent(tool_call_id=call_id))


def _streamed_text(transcript: RunTranscript, message_id: str, text: str = "") -> None:
    transcript.add(TextMessageStartEvent(message_id=message_id, role="assistant"))
    if text:
        transcript.add(TextMessageContentEvent(message_id=message_id, delta=text))
    transcript.add(TextMessageEndEvent(message_id=message_id))


def _ids(messages: list[Any]) -> list[str]:
    return [message.id for message in messages]


def test_a_streamed_id_is_lent_to_one_message_only() -> None:
    # A thinking part between two calls of one response: both calls streamed
    # under one parent, and the dump flushes them into two assistant messages.
    transcript = RunTranscript()
    _streamed_text(transcript, "p")
    _streamed_call(transcript, "c1", "p")
    _streamed_call(transcript, "c2", "p")

    restamped = transcript.with_streamed_ids(
        [_calls_message("d1", "c1"), _calls_message("d2", "c2")]
    )

    assert _ids(restamped) == ["p", "d2"]


def test_calls_streamed_under_different_parents_keep_the_dumped_id() -> None:
    transcript = RunTranscript()
    _streamed_call(transcript, "c1", "p1")
    _streamed_call(transcript, "c2", "p2")

    restamped = transcript.with_streamed_ids([_calls_message("d1", "c1", "c2")])

    assert _ids(restamped) == ["d1"]


def test_a_call_with_no_parent_keeps_the_dumped_id() -> None:
    # The protocol allows it, and the stream then announced no message id.
    transcript = RunTranscript()
    _streamed_call(transcript, "c1", None)

    restamped = transcript.with_streamed_ids([_calls_message("d1", "c1")])

    assert _ids(restamped) == ["d1"]


def test_text_answers_whose_counts_disagree_all_keep_the_dumped_ids() -> None:
    # Pairing the one streamed id with the first answer would be a guess, and a
    # wrong guess names a different message.
    transcript = RunTranscript()
    _streamed_text(transcript, "s1", "only one")

    restamped = transcript.with_streamed_ids(
        [
            AssistantMessage(id="d1", role="assistant", content="one"),
            AssistantMessage(id="d2", role="assistant", content="two"),
        ]
    )

    assert _ids(restamped) == ["d1", "d2"]


def test_an_announced_empty_text_still_counts_by_position() -> None:
    # ``messages()`` skips an empty draft; the dump keeps the empty text part, so
    # the count has to read the drafts or every answer after it falls back.
    transcript = RunTranscript()
    _streamed_text(transcript, "s1")
    _streamed_text(transcript, "s2", "answer")

    restamped = transcript.with_streamed_ids(
        [
            AssistantMessage(id="d1", role="assistant", content=""),
            AssistantMessage(id="d2", role="assistant", content="answer"),
        ]
    )

    assert _ids(restamped) == ["s1", "s2"]


def test_results_for_a_reused_call_id_pair_off_in_order() -> None:
    transcript = RunTranscript()
    transcript.add(ToolCallResultEvent(message_id="r1", tool_call_id="c1", content="first"))
    transcript.add(ToolCallResultEvent(message_id="r2", tool_call_id="c1", content="second"))

    restamped = transcript.with_streamed_ids(
        [
            ToolMessage(id="d1", role="tool", tool_call_id="c1", content="first"),
            ToolMessage(id="d2", role="tool", tool_call_id="c1", content="second"),
            ToolMessage(id="d3", role="tool", tool_call_id="c1", content="third"),
            ToolMessage(id="d4", role="tool", tool_call_id="never-streamed", content="x"),
        ]
    )

    assert _ids(restamped) == ["r1", "r2", "d3", "d4"]


def test_reasoning_pairs_by_position_and_other_messages_are_untouched() -> None:
    transcript = RunTranscript()
    transcript.add(ReasoningStartEvent(message_id="z1"))
    transcript.add(ReasoningMessageContentEvent(message_id="z1", delta="hmm"))
    user = UserMessage(id="u1", role="user", content="hi")

    restamped = transcript.with_streamed_ids(
        [user, ReasoningMessage(id="d1", role="reasoning", content="hmm")]
    )

    assert restamped[0] is user
    assert _ids(restamped) == ["u1", "z1"]


def test_a_restamped_tool_message_keeps_its_outcome() -> None:
    transcript = RunTranscript()
    transcript.add(ToolCallResultEvent(message_id="r1", tool_call_id="c1", content="no"))
    dumped = stamp_outcome(
        ToolMessage(id="d1", role="tool", tool_call_id="c1", content="no"), "denied"
    )

    (tool,) = transcript.with_streamed_ids([dumped])

    assert tool.id == "r1"
    assert tool.metadata == {OUTCOME_FIELD: "denied"}
    assert (tool.__pydantic_extra__ or {})[OUTCOME_FIELD] == "denied"


def test_reasoning_whose_text_disagrees_keeps_the_dumped_id() -> None:
    # One reasoning message each side, so the count alone would pair them.
    transcript = RunTranscript()
    transcript.add(ReasoningStartEvent(message_id="z1"))
    transcript.add(ReasoningMessageContentEvent(message_id="z1", delta="something else"))

    restamped = transcript.with_streamed_ids(
        [ReasoningMessage(id="d1", role="reasoning", content="hmm")]
    )

    assert _ids(restamped) == ["d1"]


def test_an_answer_whose_parts_the_dump_joined_still_pairs() -> None:
    # Consecutive text parts stream as one run of deltas and dump joined by a
    # newline, which the text check must not read as a different answer.
    transcript = RunTranscript()
    _streamed_text(transcript, "s1", "first part")
    transcript.add(TextMessageContentEvent(message_id="s1", delta="second part"))

    restamped = transcript.with_streamed_ids(
        [AssistantMessage(id="d1", role="assistant", content="first part\nsecond part")]
    )

    assert _ids(restamped) == ["s1"]


def test_calls_whose_parent_streamed_other_text_keep_the_dumped_id() -> None:
    # Text, thinking, then a call: the parent streamed the text with the call,
    # and the dump holds the call alone.
    transcript = RunTranscript()
    _streamed_text(transcript, "p", "A")
    _streamed_call(transcript, "c1", "p")

    restamped = transcript.with_streamed_ids([_calls_message("d1", "c1")])

    assert _ids(restamped) == ["d1"]


def test_a_signature_only_reasoning_still_pairs() -> None:
    # A thinking part carrying only a signature opens and closes reasoning with
    # no text inside, and is still dumped as a reasoning message.
    transcript = RunTranscript()
    transcript.add(ReasoningStartEvent(message_id="z1"))
    transcript.add(ReasoningEndEvent(message_id="z1"))

    restamped = transcript.with_streamed_ids(
        [ReasoningMessage(id="d1", role="reasoning", content="")]
    )

    assert _ids(restamped) == ["z1"]
