from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import FunctionModel

from django_ag_ui import AGUIServer
from tests.routineapp.models import TeamRoutine
from tests.routineapp.people import person, routine

pytestmark = pytest.mark.django_db(transaction=True)

_IDLE = FunctionModel(lambda messages, info: None)


async def _rows(user: Any, thread_ids: list[str], **options: Any) -> dict[str, Any]:
    store = DefaultConversationStore()
    request = AsyncRequestFactory().get("/agent/threads/")
    request.user = user
    for thread_id in thread_ids:
        await store.save(Conversation(thread_id, []), request=request)
    server = AGUIServer(
        ToolRegistry(), model=_IDLE, conversation_store=store, id="helper", **options
    )
    patterns, _, _ = server.urls
    view = next(pattern.callback for pattern in patterns if pattern.name == "threads")
    response = await view(request)
    return {row["thread_id"]: row["routine"] for row in json.loads(response.content)["threads"]}


async def test_a_firings_thread_row_names_its_routine() -> None:
    ann, bob = await sync_to_async(person)("ann"), await sync_to_async(person)("bob")
    mine = await sync_to_async(routine)(ann, "standup", last_thread_id="fired")
    await sync_to_async(routine)(bob, "theirs", last_thread_id="own")
    await sync_to_async(routine)(ann, "elsewhere", agent_id="other", last_thread_id="mine")
    rows = await _rows(ann, ["fired", "own", "mine"], routine_model=TeamRoutine)
    assert rows == {"fired": {"id": str(mine.pk), "name": "standup"}, "own": None, "mine": None}


async def test_the_badge_moves_to_the_newest_firing() -> None:
    ann = await sync_to_async(person)("ann")
    mine = await sync_to_async(routine)(ann, "standup", last_thread_id="first")
    mine.last_thread_id = "second"
    await mine.asave(update_fields=["last_thread_id"])
    rows = await _rows(ann, ["first", "second"], routine_model=TeamRoutine)
    assert rows == {"first": None, "second": {"id": str(mine.pk), "name": "standup"}}


async def test_a_mount_without_routines_rows_name_none() -> None:
    ann = await sync_to_async(person)("ann")
    await sync_to_async(routine)(ann, "standup", last_thread_id="fired")
    assert await _rows(ann, ["fired"]) == {"fired": None}
