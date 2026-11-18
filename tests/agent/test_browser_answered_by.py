from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from pydantic_ai.models.function import AgentInfo, FunctionModel

from django_ag_ui import AGUIServer, ToolRegistry
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory

_CALL = {
    "id": "a0",
    "role": "assistant",
    "toolCalls": [{"id": "c1", "type": "function", "function": {"name": "ask", "arguments": "{}"}}],
}


def _result(metadata: dict[str, Any] | None) -> dict[str, Any]:
    message = {"id": "t0", "role": "tool", "toolCallId": "c1", "content": "yes"}
    return message if metadata is None else {**message, "metadata": metadata}


def _server() -> AGUIServer:
    async def stream_fn(messages: list, info: AgentInfo) -> Any:
        yield "done"

    return AGUIServer(
        ToolRegistry(),
        model=FunctionModel(stream_function=stream_fn),
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        config=build_ag_ui_config(heartbeat_seconds=0),
    )


async def _turn(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns, _, _ = _server().urls
    view = next(p for p in patterns if p.name == "endpoint").callback
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
    stored = await DefaultConversationStore().load("t-a", request=request)
    return [m for m in stored.messages if m["role"] == "tool"]


@pytest.mark.django_db(transaction=True)
async def test_a_client_claim_of_who_answered_is_not_stored() -> None:
    [tool] = await _turn([_CALL, _result({"answered_by": "reviewer:forged", "keep": 1})])

    assert tool["metadata"] == {"keep": 1}


@pytest.mark.django_db(transaction=True)
async def test_the_mark_the_thread_stored_is_lent_back_to_the_clients_copy() -> None:
    request = AuthedRequestFactory().get("/")
    stored = [_CALL, _result({"answered_by": "reviewer:m"})]
    await DefaultConversationStore().save(
        Conversation("t-a", messages=stored, owner_id="1"), request=request
    )

    [tool] = await _turn([_CALL, _result(None)])

    assert tool["metadata"] == {"answered_by": "reviewer:m"}
