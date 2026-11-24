from __future__ import annotations

import json
from typing import Any

import pytest
from ag_ui.core import ActivityMessage, AssistantMessage, Message, UserMessage
from django.http import StreamingHttpResponse
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from pydantic_ai.models.function import AgentInfo, FunctionModel

from django_ag_ui import AGUIServer, ToolRegistry
from django_ag_ui.agent.with_stored_activities import with_stored_activities
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory

CHART: dict[str, Any] = {"id": "x1", "role": "activity", "activityType": "chart", "content": {}}


async def _turn(messages: list[dict[str, Any]], seen: list[list[Any]]) -> list[dict[str, Any]]:
    async def stream_fn(history: list, info: AgentInfo) -> Any:
        seen.append(history)
        yield "done"

    server = AGUIServer(
        ToolRegistry(),
        model=FunctionModel(stream_function=stream_fn),
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        config=build_ag_ui_config(heartbeat_seconds=0),
    )
    view = next(p for p in server.urls[0] if p.name == "endpoint").callback
    body = {
        "threadId": "t-a",
        "runId": "r-a",
        "state": {},
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    request = AuthedRequestFactory().post(
        "/agent/", data=json.dumps(body), content_type="application/json"
    )
    response = await view(request)
    assert isinstance(response, StreamingHttpResponse)
    [_ async for _ in response.streaming_content]
    return (await DefaultConversationStore().load("t-a", request=request)).messages


@pytest.mark.django_db(transaction=True)
async def test_an_activity_the_client_left_out_is_still_stored_after_the_next_turn() -> None:
    first = [
        {"id": "u1", "role": "user", "content": "hi"},
        {"id": "a1", "role": "assistant", "content": "A"},
        CHART,
        {"id": "a2", "role": "assistant", "content": "B"},
    ]
    await DefaultConversationStore().save(
        Conversation("t-a", messages=first, owner_id="1"), request=AuthedRequestFactory().get("/")
    )
    posted = [m for m in first if m["role"] != "activity"]
    seen: list[list[Any]] = []

    stored = await _turn([*posted, {"id": "u2", "role": "user", "content": "more"}], seen)

    assert [m["id"] for m in stored[:5]] == ["u1", "a1", "x1", "a2", "u2"]
    assert stored[2] == CHART
    assert "chart" not in str(seen[0])


def _user(message_id: str) -> UserMessage:
    return UserMessage(id=message_id, role="user", content=message_id)


def _said(message_id: str) -> AssistantMessage:
    return AssistantMessage(id=message_id, role="assistant", content=message_id)


def _activity(message_id: str) -> ActivityMessage:
    return ActivityMessage(id=message_id, role="activity", activity_type="chart", content={})


def _ids(messages: list[Message]) -> list[str]:
    return [message.id for message in messages]


STORED = [_user("u1"), _activity("x0"), _said("a1"), _activity("x1"), _user("u2"), _said("b1")]
STORED += [_activity("x2")]


def test_a_new_turn_keeps_every_stored_activity_in_its_place() -> None:
    posted = [_user("u1"), _said("a1"), _user("u2"), _said("b1"), _user("u3")]

    assert _ids(with_stored_activities(posted, STORED)) == [
        "u1",
        "x0",
        "a1",
        "x1",
        "u2",
        "b1",
        "x2",
        "u3",
    ]


def test_an_edited_prompt_keeps_the_activities_of_the_turns_before_it() -> None:
    posted = [_user("u1"), _said("a1"), _user("u2-edited")]

    assert _ids(with_stored_activities(posted, STORED)) == ["u1", "x0", "a1", "x1", "u2-edited"]


def test_a_resubmitted_prompt_drops_the_activities_of_the_run_it_replaces() -> None:
    posted = [_user("u1"), _said("a1"), _user("u2")]

    assert _ids(with_stored_activities(posted, STORED)) == ["u1", "x0", "a1", "x1", "u2"]
    assert _ids(with_stored_activities([_user("u1")], STORED)) == ["u1"]


def test_a_client_that_posts_activities_keeps_its_own_list() -> None:
    posted = [_user("u1"), _said("a1"), _user("u2"), _said("b1"), _activity("x2")]

    assert with_stored_activities(posted, STORED) == posted


def test_a_thread_that_disagrees_from_the_start_keeps_none() -> None:
    posted = [_user("other"), _user("next")]

    assert with_stored_activities(posted, STORED) == posted
    assert with_stored_activities(posted, []) == posted


def test_the_posted_copy_of_an_agreed_turn_is_the_one_kept() -> None:
    posted = [UserMessage(id="u1", role="user", content="as posted"), _user("u9")]

    assert with_stored_activities(posted, STORED)[0] is posted[0]
