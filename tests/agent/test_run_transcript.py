from __future__ import annotations

from ag_ui.core import (
    AssistantMessage,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    ToolMessage,
)

from django_ag_ui.agent.run_transcript import RunTranscript


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
