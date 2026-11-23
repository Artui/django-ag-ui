from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import connection
from django.test import AsyncRequestFactory
from django.test.utils import CaptureQueriesContext
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import FunctionModel

from django_ag_ui import AGUIServer
from tests.routineapp.models import ShiftRoutine, TeamRoutine
from tests.routineapp.people import WEEKDAYS_AT_8, person, routine

pytestmark = pytest.mark.django_db(transaction=True)

_IDLE = FunctionModel(lambda messages, info: None)


async def _rows(user: Any, threads: dict[str, Any], **options: Any) -> dict[str, Any]:
    store = DefaultConversationStore()
    request = AsyncRequestFactory().get("/agent/threads/")
    request.user = user
    for thread_id, fired_by in threads.items():
        routine_id = None if fired_by is None else str(getattr(fired_by, "pk", fired_by))
        await store.save(Conversation(thread_id, [], routine_id=routine_id), request=request)
    server = AGUIServer(
        ToolRegistry(), model=_IDLE, conversation_store=store, id="helper", **options
    )
    patterns, _, _ = server.urls
    view = next(pattern.callback for pattern in patterns if pattern.name == "threads")
    response = await view(request)
    return {row["thread_id"]: row["routine"] for row in json.loads(response.content)["threads"]}


async def test_a_firings_thread_row_names_its_routine() -> None:
    ann, bob = await sync_to_async(person)("ann"), await sync_to_async(person)("bob")
    mine = await sync_to_async(routine)(ann, "standup")
    theirs = await sync_to_async(routine)(bob, "theirs")
    elsewhere = await sync_to_async(routine)(ann, "elsewhere", agent_id="other")
    threads = {"fired": mine, "own": theirs, "mine": elsewhere, "chat": None, "gone": "999"}
    rows = await _rows(ann, threads, routine_model=TeamRoutine)
    named = {"id": str(mine.pk), "name": "standup"}
    assert rows == {"fired": named, "own": None, "mine": None, "chat": None, "gone": None}


async def test_a_routine_fired_twice_names_it_on_both_threads() -> None:
    ann = await sync_to_async(person)("ann")
    mine = await sync_to_async(routine)(ann, "standup", last_thread_id="second")
    rows = await _rows(ann, {"first": mine, "second": mine}, routine_model=TeamRoutine)
    named = {"id": str(mine.pk), "name": "standup"}
    assert rows == {"first": named, "second": named}


async def test_a_mount_without_routines_rows_name_none() -> None:
    ann = await sync_to_async(person)("ann")
    mine = await sync_to_async(routine)(ann, "standup", last_thread_id="fired")
    assert await _rows(ann, {"fired": mine}) == {"fired": None}


async def test_a_model_whose_manager_is_not_called_objects_names_its_routine() -> None:
    ann = await sync_to_async(person)("ann")
    mine = await ShiftRoutine.rows.acreate(
        principal=ann,
        created_by=ann,
        agent_id="helper",
        name="standup",
        prompt="Run standup.",
        schedule=WEEKDAYS_AT_8,
        timezone="Europe/Paris",
    )
    assert not hasattr(ShiftRoutine, "objects")
    rows = await _rows(ann, {"fired": mine}, routine_model=ShiftRoutine)
    assert rows == {"fired": {"id": str(mine.pk), "name": "standup"}}


def test_a_page_names_its_routines_in_one_query() -> None:
    ann = person("ann")
    first, second = routine(ann, "standup"), routine(ann, "triage")
    threads = {"a": first, "b": second, "c": first}
    with CaptureQueriesContext(connection) as queries:
        rows = async_to_sync(_rows)(ann, threads, routine_model=TeamRoutine)
    assert [row["name"] for row in rows.values()] == ["standup", "triage", "standup"]
    table = TeamRoutine._meta.db_table
    assert len([q for q in queries if f'FROM "{table}"' in q["sql"]]) == 1
