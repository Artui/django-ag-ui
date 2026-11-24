from __future__ import annotations

from typing import Any

from ag_ui.core import ActivityMessage, ActivitySnapshotEvent, ToolMessage, UserMessage
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.ag_ui._utils import ENQUEUED_MESSAGES_ACTIVITY_TYPE as UPSTREAM_TYPE

from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.constants import ENQUEUED_MESSAGES_ACTIVITY_TYPE
from tests.agent.test_stored_ids_match_the_stream import (
    USER_TURN,
    _after_a_tool_return,
    _assert_unique,
    _new,
    _run,
    _session,
    _Store,
)


def _nudging_agent(nudge: Any, *, seen: list[list[Any]] | None = None) -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        if seen is not None:
            seen.append(messages)
        if _after_a_tool_return(messages):
            yield "noted"
            return
        if not isinstance(messages[-1].parts[-1], UserPromptPart) or len(messages) > 1:
            yield "again"
            return
        yield {0: DeltaToolCall(name="nudge", json_args="{}", tool_call_id="c1")}

    agent: Agent[None, Any] = Agent(FunctionModel(stream_function=stream_fn))
    agent.tool(nudge, name="nudge")
    return agent


def _deliveries(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        frame
        for frame in frames
        if frame["type"] == "ACTIVITY_SNAPSHOT" and frame["activityType"] == UPSTREAM_TYPE
    ]


def test_the_activity_type_is_the_one_pydantic_ai_streams() -> None:
    assert ENQUEUED_MESSAGES_ACTIVITY_TYPE == UPSTREAM_TYPE


async def test_a_delivery_beside_a_tool_return_is_stored_as_the_activity_it_streamed() -> None:
    def nudge(ctx: RunContext[Any]) -> str:
        ctx.enqueue("a note from the tool")
        return "ok"

    store = _Store()

    frames = await _run(_session(_nudging_agent(nudge), store))

    (delivery,) = _deliveries(frames)
    (conversation,) = store.saved
    stored = _new(conversation, after=1)
    assert [m["role"] for m in stored] == ["assistant", "tool", "activity", "assistant"]
    assert stored[2] == {
        "id": delivery["messageId"],
        "role": "activity",
        "activityType": UPSTREAM_TYPE,
        "content": delivery["content"],
    }
    _assert_unique(conversation)


async def test_deliveries_of_their_own_keep_the_enqueue_ids_after_a_priority_change() -> None:
    enqueued: list[str | None] = []

    def nudge(ctx: RunContext[Any]) -> str:
        enqueued.append(ctx.enqueue("idle note", priority="when_idle"))
        enqueued.append(moved := ctx.enqueue("moved note"))
        assert moved is not None and ctx.set_enqueue_priority(moved, "when_idle")
        dropped = ctx.enqueue("dropped note", priority="when_idle")
        assert dropped is not None and ctx.dequeue(dropped)
        return "ok"

    store = _Store()

    frames = await _run(_session(_nudging_agent(nudge), store))

    assert [frame["messageId"] for frame in _deliveries(frames)] == enqueued
    (conversation,) = store.saved
    stored = _new(conversation, after=1)
    assert [m["id"] for m in stored if m["role"] == "activity"] == enqueued
    assert [m["role"] for m in stored].count("user") == 0
    assert "dropped note" not in str(conversation.messages)


async def test_a_delivery_posted_back_reaches_the_model_once_and_is_stored_once() -> None:
    def nudge(ctx: RunContext[Any]) -> str:
        ctx.enqueue("a note from the tool")
        return "ok"

    store = _Store()
    await _run(_session(_nudging_agent(nudge), store))
    first = store.saved[0].messages
    seen: list[list[Any]] = []

    await _run(
        _session(
            _nudging_agent(nudge, seen=seen),
            store,
            messages=[*first, {"id": "u2", "role": "user", "content": "and now?"}],
        )
    )

    prompts = [
        part.content
        for message in seen[0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert prompts.count("a note from the tool") == 1
    second = store.saved[1].messages
    assert [m["id"] for m in second if m["role"] == "activity"] == [
        m["id"] for m in first if m["role"] == "activity"
    ]
    _assert_unique(store.saved[1])
    assert USER_TURN in second


async def test_a_delivery_survives_the_next_turn_a_client_posts_without_it() -> None:
    def nudge(ctx: RunContext[Any]) -> str:
        ctx.enqueue("a note from the tool")
        return "ok"

    store = _Store()
    await _run(_session(_nudging_agent(nudge), store))
    first = store.saved[0].messages
    posted = [m for m in first if m["role"] != "activity"]

    await _run(
        _session(
            _nudging_agent(nudge),
            store,
            messages=[*posted, {"id": "u2", "role": "user", "content": "and now?"}],
            stored=first,
        )
    )

    (delivery,) = [m for m in first if m["role"] == "activity"]
    second = store.saved[1].messages
    assert second[: len(first)] == first
    assert delivery in second
    _assert_unique(store.saved[1])


def _delivered(transcript: RunTranscript, message_id: str, *texts: str) -> None:
    transcript.add(
        ActivitySnapshotEvent(
            message_id=message_id,
            activity_type=UPSTREAM_TYPE,
            content={
                "messages": [
                    {"id": f"{message_id}-{i}", "role": "user", "content": text}
                    for i, text in enumerate(texts)
                ]
            },
        )
    )


def _user(message_id: str, text: str) -> UserMessage:
    return UserMessage(id=message_id, role="user", content=text)


def test_a_delivery_takes_the_dumped_messages_it_holds_in_one_activity() -> None:
    transcript = RunTranscript()
    _delivered(transcript, "e1", "one", "two")
    tool = ToolMessage(id="t1", role="tool", content="ok", tool_call_id="c1")

    restamped = transcript.with_streamed_ids([tool, _user("d1", "one"), _user("d2", "two")])

    assert restamped[0] == tool
    assert isinstance(restamped[1], ActivityMessage)
    assert (restamped[1].id, len(restamped)) == ("e1", 2)


def test_deliveries_are_matched_in_stream_order_past_one_another() -> None:
    transcript = RunTranscript()
    _delivered(transcript, "e1", "same")
    _delivered(transcript, "e2", "same")

    restamped = transcript.with_streamed_ids([_user("d1", "same"), _user("d2", "same")])

    assert [message.id for message in restamped] == ["e1", "e2"]


def test_an_activity_of_another_type_does_not_stop_a_delivery_being_matched() -> None:
    transcript = RunTranscript()
    transcript.add(ActivitySnapshotEvent(message_id="s1", activity_type="a2ui-surface", content={}))
    _delivered(transcript, "e1", "note")

    restamped = transcript.with_streamed_ids([_user("d1", "note")])

    assert [message.id for message in restamped] == ["e1"]


def test_one_unmatched_delivery_leaves_every_delivery_as_dumped() -> None:
    transcript = RunTranscript()
    _delivered(transcript, "e1", "kept")
    _delivered(transcript, "e2", "not in the dump")
    dumped = [_user("d1", "kept"), _user("d2", "other words")]

    assert transcript.with_streamed_ids(dumped) == dumped


def test_a_delivery_with_no_messages_takes_nothing() -> None:
    transcript = RunTranscript()
    _delivered(transcript, "e1")
    dumped = [_user("d1", "the user's own words")]

    assert transcript.with_streamed_ids(dumped) == dumped


def test_deliveries_the_dump_holds_out_of_stream_order_are_left_as_dumped() -> None:
    transcript = RunTranscript()
    _delivered(transcript, "e1", "first")
    _delivered(transcript, "e2", "second")
    dumped = [_user("d1", "second"), _user("d2", "first")]

    assert transcript.with_streamed_ids(dumped) == dumped
