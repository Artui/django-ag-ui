from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import override_settings
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.contrib.store.models import StoredConversation
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.persistence.threads_view import ThreadsView
from tests.agent.test_agui_view import _drain, _post, _registry
from tests.authed_request_factory import AuthedRequestFactory, authenticated_user

ENABLED = override_settings(DJANGO_AG_UI={"A2UI": {"ENABLED": True}})

# Literals from the middleware's own recipe, computed in Node, never by the code under test.
PIN_RUN_ID = "run-pin"
PIN_ID = "a2ui-action-user-9e2c5042d409ca0c2f0107c8eb526ac5a1a1972670f10f8539a6703d91470fb7"
PIN_NAME = "a2ui_action_context_nixQQtQJygwvAQfI61JqxaGhlyZw8Q-FOaZwPZFHD7c"
PIN_ACTION: dict[str, Any] = {
    "name": "book",
    "surfaceId": "s1",
    "context": {"seats": 2, "city": "Zürich"},
}
PIN_CONTENT = (
    'A2UI user action:\n{"name":"book","surfaceId":"s1","context":{"seats":2,"city":"Zürich"}}'
)


def _body(
    messages: list[dict[str, Any]],
    *,
    run_id: str,
    forwarded: Any = None,
    tools: list[dict[str, Any]] | None = None,
) -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": run_id,
            "state": {},
            "messages": messages,
            "tools": tools or [],
            "context": [],
            "forwardedProps": forwarded,
        }
    ).encode()


def _action(user_action: Any) -> dict[str, Any]:
    return {"a2uiAction": {"userAction": user_action}}


def _prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


def _recording(seen: list[list[ModelMessage]], *, tool: str | None = None) -> FunctionModel:
    """Answers in text; with ``tool``, first calls that client tool and stops for its result."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> Any:
        seen.append(list(messages))
        last = messages[-1]
        answered = isinstance(last, ModelRequest) and any(
            part.part_kind == "tool-return" for part in last.parts
        )
        if tool is not None and not answered:
            yield {0: DeltaToolCall(name=tool, json_args="{}", tool_call_id="call-seat")}
            return
        yield "booked"

    return FunctionModel(stream_function=stream)


def _view(model: FunctionModel, store: Any = None) -> DjangoAGUIView:
    return DjangoAGUIView(
        _registry(),
        model=model,
        conversation_store=store if store is not None else DefaultConversationStore(),
    )


async def _stored() -> list[dict[str, Any]]:
    row = await StoredConversation.objects.aget(thread_id="t1")
    return row.messages


def _turn(content: str = PIN_CONTENT) -> dict[str, Any]:
    return {"id": PIN_ID, "role": "user", "name": PIN_NAME, "content": content}


def _without_nulls(message: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in message.items() if value is not None}


# --- The turn's identity and content are a public contract ----------------------


@ENABLED
@pytest.mark.django_db(transaction=True)
async def test_the_action_turn_is_pinned_and_reaches_the_model_and_the_store() -> None:
    seen: list[list[ModelMessage]] = []
    body = await _drain(
        await _view(_recording(seen))(
            _post(_body([], run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION)))
        )
    )

    assert "RUN_FINISHED" in body
    assert _prompts(seen[0]) == [PIN_CONTENT]
    stored = await _stored()
    assert _without_nulls(stored[0]) == _turn()
    assert [message["role"] for message in stored] == ["user", "assistant"]
    replay = await ThreadsView(DefaultConversationStore())(
        AuthedRequestFactory().get("/agent/threads/t1/"), thread_id="t1"
    )
    assert _without_nulls(json.loads(replay.content)["messages"][0]) == _turn()


@ENABLED
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("user_message", "expected"),
    [
        ("Book two seats", "Book two seats\n\n"),
        ("  ", "  \n\n"),
        ("", ""),
        (7, ""),
    ],
    ids=["text", "whitespace-taken-as-given", "empty", "not-text"],
)
async def test_a_user_message_leads_the_turn_when_it_is_a_non_empty_string(
    user_message: Any, expected: str
) -> None:
    seen: list[list[ModelMessage]] = []
    action = {"name": "book", "userMessage": user_message}
    await _drain(
        await _view(_recording(seen))(
            _post(_body([], run_id=PIN_RUN_ID, forwarded=_action(action)))
        )
    )

    block = "A2UI user action:\n" + json.dumps(action, separators=(",", ":"), ensure_ascii=False)
    assert _prompts(seen[0]) == [expected + block]


@ENABLED
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "forwarded",
    [
        None,
        {},
        {"other": 1},
        ["a2uiAction"],
        {"a2uiAction": "click"},
        {"a2uiAction": {}},
        _action("click"),
        _action(None),
        _action(["click"]),
    ],
    ids=[
        "none",
        "empty",
        "other-props",
        "props-not-an-object",
        "action-not-an-object",
        "no-user-action",
        "user-action-text",
        "user-action-null",
        "user-action-list",
    ],
)
async def test_the_stage_fires_only_for_a_well_formed_action(forwarded: Any) -> None:
    seen: list[list[ModelMessage]] = []
    hello = {"id": "u1", "role": "user", "content": "hello"}
    body = await _drain(
        await _view(_recording(seen))(_post(_body([hello], run_id="r1", forwarded=forwarded)))
    )

    assert "RUN_FINISHED" in body
    assert _prompts(seen[0]) == ["hello"]
    assert [message["id"] for message in await _stored()][:1] == ["u1"]
    assert len(await _stored()) == 2


@pytest.mark.django_db(transaction=True)
async def test_a_mount_that_has_not_opted_in_ignores_the_action() -> None:
    seen: list[list[ModelMessage]] = []
    hello = {"id": "u1", "role": "user", "content": "hello"}
    await _drain(
        await _view(_recording(seen))(
            _post(_body([hello], run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION)))
        )
    )

    assert _prompts(seen[0]) == ["hello"]
    assert PIN_ID not in [message["id"] for message in await _stored()]


# --- Later requests: the client carries the turn under its server id ------------


@ENABLED
@pytest.mark.django_db(transaction=True)
async def test_a_later_run_carrying_the_turn_stores_it_once() -> None:
    seen: list[list[ModelMessage]] = []
    view = _view(_recording(seen))
    await _drain(await view(_post(_body([], run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION)))))
    held = await _stored()

    later = [*held, {"id": "u2", "role": "user", "content": "and lunch"}]
    await _drain(await view(_post(_body(later, run_id="run-next"))))

    stored = await _stored()
    assert [message["id"] for message in stored][:3] == [PIN_ID, held[1]["id"], "u2"]
    assert [message["role"] for message in stored] == ["user", "assistant", "user", "assistant"]
    assert _prompts(seen[-1]) == [PIN_CONTENT, "and lunch"]


@ENABLED
@pytest.mark.django_db(transaction=True)
async def test_a_retry_of_the_same_run_does_not_duplicate_the_turn() -> None:
    seen: list[list[ModelMessage]] = []
    view = _view(_recording(seen))
    await _drain(await view(_post(_body([], run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION)))))

    retried = [_turn()]
    await _drain(
        await view(_post(_body(retried, run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION))))
    )

    assert _prompts(seen[-1]) == [PIN_CONTENT]
    assert [message["id"] for message in await _stored()].count(PIN_ID) == 1


@ENABLED
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "copy",
    [
        {"id": "client-copy", "role": "user", "name": PIN_NAME},
        {"id": PIN_ID, "role": "user"},
    ],
    ids=["by-name", "by-id"],
)
async def test_a_copy_matching_by_id_or_name_is_the_same_turn_whatever_its_content(
    copy: dict[str, Any],
) -> None:
    seen: list[list[ModelMessage]] = []
    spelled = {**copy, "content": 'A2UI user action:\n{"seats":1}'}
    await _drain(
        await _view(_recording(seen))(
            _post(_body([spelled], run_id=PIN_RUN_ID, forwarded=_action({"seats": 1.0})))
        )
    )

    assert _prompts(seen[-1]) == ['A2UI user action:\n{"seats":1}']


@ENABLED
@pytest.mark.django_db(transaction=True)
async def test_an_assistant_message_under_the_turns_id_does_not_count() -> None:
    seen: list[list[ModelMessage]] = []
    hello = {"id": "u1", "role": "user", "content": "hello"}
    impostor = {"id": PIN_ID, "role": "assistant", "content": "earlier"}
    await _drain(
        await _view(_recording(seen))(
            _post(_body([hello, impostor], run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION)))
        )
    )

    assert _prompts(seen[-1]) == ["hello", PIN_CONTENT]


@ENABLED
@pytest.mark.django_db(transaction=True)
async def test_a_new_run_carrying_an_action_is_a_new_turn() -> None:
    seen: list[list[ModelMessage]] = []
    view = _view(_recording(seen))
    await _drain(await view(_post(_body([], run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION)))))
    held = await _stored()

    await _drain(await view(_post(_body(held, run_id="run-again", forwarded=_action(PIN_ACTION)))))

    actions = [m for m in await _stored() if m.get("name", "").startswith("a2ui_action_context_")]
    assert [m["id"] == PIN_ID for m in actions] == [True, False]
    assert _prompts(seen[-1]) == [PIN_CONTENT, PIN_CONTENT]


SEAT_TOOL = {"name": "pick_seat", "description": "Pick a seat.", "parameters": {"type": "object"}}
PRIOR = [
    {"id": "u0", "role": "user", "content": "show me the booking form"},
    {"id": "a0", "role": "assistant", "content": "here it is"},
]


async def _action_then_client_tool(seen: list[list[ModelMessage]]) -> list[dict[str, Any]]:
    """Run the action, whose answer calls a client tool; return what the store holds."""
    view = _view(_recording(seen, tool="pick_seat"))
    body = _body(PRIOR, run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION), tools=[SEAT_TOOL])
    await _drain(await view(_post(body)))
    return await _stored()


def _tool_result() -> dict[str, Any]:
    return {"id": "r-seat", "role": "tool", "toolCallId": "call-seat", "content": "12A"}


@ENABLED
@pytest.mark.django_db(transaction=True)
async def test_a_follow_up_round_carrying_the_turn_keeps_it_in_place() -> None:
    seen: list[list[ModelMessage]] = []
    held = await _action_then_client_tool(seen)
    assert [message["id"] for message in held][:3] == ["u0", "a0", PIN_ID]
    (call,) = held[3:]
    assert call["role"] == "assistant"

    follow_up = [*held, _tool_result()]
    view = _view(_recording(seen))
    await _drain(await view(_post(_body(follow_up, run_id="run-follow", tools=[SEAT_TOOL]))))

    stored = await _stored()
    assert [message["id"] for message in stored][:5] == ["u0", "a0", PIN_ID, call["id"], "r-seat"]
    assert [message["role"] for message in stored[5:]] == ["assistant"]
    assert [message["id"] for message in stored].count(PIN_ID) == 1
    assert PIN_CONTENT in _prompts(seen[-1])


@ENABLED
@pytest.mark.django_db(transaction=True)
async def test_a_follow_up_round_without_the_turn_drops_it() -> None:
    # Characterisation: the store keeps what the client posts, so an omitted turn is gone.
    seen: list[list[ModelMessage]] = []
    held = await _action_then_client_tool(seen)

    follow_up = [*[m for m in held if m["id"] != PIN_ID], _tool_result()]
    view = _view(_recording(seen))
    await _drain(await view(_post(_body(follow_up, run_id="run-follow", tools=[SEAT_TOOL]))))

    stored = await _stored()
    assert [message["id"] for message in stored][:4] == ["u0", "a0", held[3]["id"], "r-seat"]
    assert [message["role"] for message in stored[4:]] == ["assistant"]
    assert _prompts(seen[-1]) == ["show me the booking form"]


# --- Server-held history: a resumed run takes the action as its new turn --------


@ENABLED
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("replays", [True, False], ids=["client-replays-the-row", "server-only"])
async def test_a_resumed_run_takes_the_action_as_its_new_turn(replays: bool) -> None:
    seen: list[list[ModelMessage]] = []
    view = DjangoAGUIView(
        _registry(),
        model=_recording(seen),
        step_store=DefaultStepStore,
        conversation_store=DefaultConversationStore(),
        get_user=lambda _request: authenticated_user(),
    )
    hello = {"id": "u1", "role": "user", "content": "hello"}
    await _drain(await view(_post(_body([hello], run_id="run-first"))))
    first = await _stored()

    posted = first if replays else []
    await _drain(
        await view(
            _post(_body(posted, run_id=PIN_RUN_ID, forwarded=_action(PIN_ACTION))),
            resume_from="run-first",
        )
    )

    assert _prompts(seen[-1]) == ["hello", PIN_CONTENT]
    stored = await _stored()
    assert [message["id"] for message in stored][:3] == ["u1", first[1]["id"], PIN_ID]
    assert [message["role"] for message in stored] == ["user", "assistant", "user", "assistant"]
