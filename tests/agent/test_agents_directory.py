from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import StreamingHttpResponse
from django.test import override_settings
from django.urls import reverse
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.conversation_meta import ConversationMeta
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_waiting import RunWaiting
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui import AgentsDirectory
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.persistence import fresh_list_response as fresh_list_response_module
from tests.agent import agents_directory_urls as urls
from tests.authed_request_factory import AuthedAsyncRequestFactory, authenticated_user
from tests.persistence.test_thread_status import _frames

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _urlconf() -> Iterator[None]:
    with override_settings(ROOT_URLCONF="tests.agent.agents_directory_urls"):
        yield


_TOKEN = {"Authorization": urls.TOKEN}
_LEASE = timedelta(minutes=5)


def _route(directory: AgentsDirectory, name: str) -> Any:
    patterns, _, _ = directory.urls
    return next(p for p in patterns if p.name == name).callback


def _request(path: str = "/", *, user: Any = None, **headers: str) -> Any:
    request = AuthedAsyncRequestFactory().get(path, headers=headers)
    if user is not None:
        request.user = user
    return request


async def _get(directory: AgentsDirectory, name: str, **kwargs: Any) -> Any:
    return await _route(directory, name)(_request(**kwargs))


async def _save(thread_id: str, user: Any) -> None:
    request = _request(user=user)
    await DefaultConversationStore().save(
        Conversation(thread_id=thread_id, messages=[]), request=request
    )


def _body(response: Any) -> dict[str, Any]:
    return json.loads(response.content)


def _server(**kwargs: Any) -> AGUIServer:
    return AGUIServer(ToolRegistry(), model=TestModel(), csrf_exempt=True, **kwargs)


@pytest.mark.parametrize(
    ("servers", "message"),
    [
        ([], "at least one"),
        ([_server(namespace="a")], "id="),
        ([_server(id="x", namespace="a"), _server(id="x", namespace="b")], "'x' is repeated"),
        ([_server(id="x", namespace="a"), _server(id="y", namespace="a")], "'a' is repeated"),
    ],
    ids=["empty", "no-id", "repeated-id", "repeated-namespace"],
)
def test_construction_refuses_an_ambiguous_mount_set(servers: list[Any], message: str) -> None:
    with pytest.raises(ImproperlyConfigured, match=message):
        AgentsDirectory(servers)


def test_urls_mount_agents_and_inbox_under_the_directory_namespace() -> None:
    patterns, app_name, namespace = AgentsDirectory([urls.spa]).urls

    assert {p.name: str(p.pattern) for p in patterns} == {
        "agents": "agents/",
        "inbox": "inbox/",
    }
    assert (app_name, namespace) == ("ag_ui_agents", "ag_ui_agents")
    assert reverse("dir:inbox") == "/home/inbox/"


async def test_each_entry_carries_its_identity_and_the_mounted_routes_reversed() -> None:
    response = await _get(urls.forward, "agents", **_TOKEN)

    spa, admin = _body(response)["agents"]
    assert spa == {
        "id": "spa",
        "name": "Board",
        "description": "The board agent",
        "endpoint": reverse("spa:endpoint"),
        "tools_url": reverse("spa:tools"),
        "threads_url": reverse("spa:threads"),
        "runs_url": None,
        "skills_url": reverse("spa:skills"),
        "skill_editor_url": None,
        "routines_url": None,
        "attachments_url": None,
        "transcribe_url": None,
    }
    assert admin["id"] == "admin"
    assert admin["threads_url"] == "/admin-agent/threads/"
    assert admin["skills_url"] is None
    assert response["ETag"]


async def test_a_mount_without_threads_is_listed_with_null_threads_and_no_rows() -> None:
    directory = AgentsDirectory([urls.bare])

    (entry,) = _body(await _get(directory, "agents"))["agents"]
    rows = _body(await _get(directory, "inbox"))["threads"]

    assert entry["endpoint"] == "/bare/"
    assert entry["tools_url"] == "/bare/tools/"
    assert entry["threads_url"] is None
    assert rows == []


async def test_a_store_refusing_an_anonymous_caller_is_a_403_denial() -> None:
    open_mount = _server(
        id="open",
        namespace="open",
        require_authenticated=False,
        conversation_store=DefaultConversationStore(),
    )
    anonymous = SimpleNamespace(is_authenticated=False, pk=None)

    listed = await open_mount.thread_rows(_request(user=anonymous))

    assert listed == 403


async def test_routes_are_the_mounted_pattern_names_in_order() -> None:
    names = urls.spa.routes()

    assert names[:3] == ("endpoint", "tools", "mcp_apps")
    assert "threads" in names and "runs" not in names
    assert urls.spa.routes() is names
    assert urls.spa.namespace == "spa"


async def test_a_denying_mount_is_absent_from_both_routes() -> None:
    await _save("own-1", authenticated_user(pk=1))

    agents = _body(await _get(urls.forward, "agents", **_TOKEN))["agents"]
    rows = _body(await _get(urls.forward, "inbox", **_TOKEN))["threads"]

    assert [entry["id"] for entry in agents] == ["spa", "admin"]
    assert {row["agent_id"] for row in rows} == {"admin"}


@pytest.mark.parametrize("name", ["agents", "inbox"])
async def test_no_admitting_mount_answers_401_unless_every_denial_was_403(name: str) -> None:
    anonymous = SimpleNamespace(is_authenticated=False, pk=None)

    all_401 = await _get(urls.forward, name, user=anonymous)
    mixed = await _get(AgentsDirectory([urls.denier, urls.spa]), name)
    forbidden = await _get(urls.only_denier, name)

    assert all_401.status_code == 401
    assert mixed.status_code == 401
    assert forbidden.status_code == 403


@pytest.mark.parametrize("directory", [urls.forward, urls.backward], ids=["forward", "reversed"])
async def test_each_mount_lists_under_its_own_principal_in_either_order(
    directory: AgentsDirectory,
) -> None:
    await _save("session-1", authenticated_user(pk=1))
    await _save("token-2", authenticated_user(pk=2, username="token"))

    rows = _body(await _get(directory, "inbox", **_TOKEN))["threads"]

    by_mount: dict[str, set[str]] = {}
    for row in rows:
        by_mount.setdefault(row["agent_id"], set()).add(row["thread_id"])
    assert by_mount == {"spa": {"token-2"}, "admin": {"session-1"}}


class _StubStore(DefaultConversationStore):
    """Lists fixed metas, so a row can have no ``updated_at``."""

    def __init__(self, *metas: ConversationMeta) -> None:
        self._metas = list(metas)

    async def list(self, *, request: Any, limit: int = 50) -> list[ConversationMeta]:
        return self._metas[:limit]


def _meta(thread_id: str, minutes: int | None) -> ConversationMeta:
    stamp = (
        None if minutes is None else datetime(2026, 11, 23, tzinfo=UTC) + timedelta(minutes=minutes)
    )
    return ConversationMeta(thread_id=thread_id, title=thread_id, updated_at=stamp)


async def test_inbox_merges_newest_first_with_nulls_last_and_ties_in_server_order() -> None:
    first = _server(
        id="one",
        namespace="one",
        conversation_store=_StubStore(_meta("a-none", None), _meta("a-5", 5), _meta("a-1", 1)),
    )
    second = _server(
        id="two",
        namespace="two",
        conversation_store=_StubStore(_meta("b-none", None), _meta("b-5", 5), _meta("b-9", 9)),
    )

    rows = _body(await _get(AgentsDirectory([first, second]), "inbox"))["threads"]

    assert [row["thread_id"] for row in rows] == ["b-9", "a-5", "b-5", "a-1", "a-none", "b-none"]
    assert [row["agent_id"] for row in rows][:2] == ["two", "one"]


@pytest.mark.parametrize(("query", "count"), [("?limit=1", 1), ("?limit=0", 3), ("?limit=x", 3)])
async def test_limit_caps_each_mounts_share_not_the_merge(query: str, count: int) -> None:
    metas = [_meta(f"t{n}", n) for n in range(3)]
    first = _server(id="one", namespace="one", conversation_store=_StubStore(*metas))
    second = _server(id="two", namespace="two", conversation_store=_StubStore(*metas))
    directory = AgentsDirectory([first, second])

    rows = _body(await _route(directory, "inbox")(_request(f"/inbox/{query}")))["threads"]

    assert len(rows) == 2 * count


async def test_a_waiting_row_equals_its_mounts_own_threads_row() -> None:
    owner = _request(user=authenticated_user(pk=2, username="token"))
    await _save("token-2", owner.user)
    run_store = urls.spa._run_store
    await run_store.claim("token-2", "r1", request=owner, lease=_LEASE)
    waiting = (
        RunWaiting(kind="approval", summary="Delete?", tool_call_id="c-1", destructive=True),
    )
    await run_store.finish("token-2", "r1", RunOutcome.WAITING, request=owner, waiting=waiting)

    (inbox_row,) = _body(await _get(urls.forward, "inbox", **_TOKEN))["threads"]
    threads = await _route_of(urls.spa, "threads")(_request(**_TOKEN))
    (own_row,) = _body(threads)["threads"]

    assert inbox_row == own_row
    assert inbox_row["status"] == "waiting"
    assert inbox_row["interrupts"][0]["toolCallId"] == "c-1"


def _route_of(server: AGUIServer, name: str) -> Any:
    patterns, _, _ = server.urls
    return next(p for p in patterns if p.name == name).callback


@pytest.mark.parametrize("name", ["agents", "inbox"])
async def test_a_matching_if_none_match_answers_304(name: str) -> None:
    await _save("session-1", authenticated_user(pk=1))
    first = await _get(urls.forward, name, **_TOKEN)

    again = await _get(urls.forward, name, **_TOKEN, **{"If-None-Match": first["ETag"]})

    assert first.status_code == 200
    assert again.status_code == 304
    assert again["ETag"] == first["ETag"]
    assert again["Cache-Control"] == "private, no-cache"


async def test_agents_answers_a_stream_request_as_a_poll() -> None:
    response = await _get(urls.forward, "agents", **_TOKEN, Accept="text/event-stream")

    assert not isinstance(response, StreamingHttpResponse)
    assert response["Content-Type"] == "application/json"


async def test_an_inbox_frame_carries_the_poll_body_and_its_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fresh_list_response_module, "_STREAM_POLL_SECONDS", 0.01)
    await _save("session-1", authenticated_user(pk=1))
    polled = await _get(urls.forward, "inbox", **_TOKEN)

    response = await _get(urls.forward, "inbox", **_TOKEN, Accept="text/event-stream")
    assert isinstance(response, StreamingHttpResponse)
    stream = response.streaming_content
    first = await _frames(stream, 1)
    await _save("session-2", authenticated_user(pk=1))
    second = await asyncio.wait_for(_frames(stream, 1), timeout=5)
    await stream.aclose()

    assert first == [(polled["ETag"], _body(polled))]
    assert {row["thread_id"] for row in second[0][1]["threads"]} == {"session-1", "session-2"}


async def test_a_stream_whose_caller_loses_every_mount_sends_an_empty_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fresh_list_response_module, "_STREAM_POLL_SECONDS", 0.01)
    await _save("session-1", authenticated_user(pk=1))
    request = _request(Accept="text/event-stream")
    response = await _route(urls.forward, "inbox")(request)
    stream = response.streaming_content
    await _frames(stream, 1)

    request.user = SimpleNamespace(is_authenticated=False, pk=None)
    (frame,) = await asyncio.wait_for(_frames(stream, 1), timeout=5)
    await stream.aclose()

    assert frame[1] == {"threads": []}


@pytest.mark.parametrize("name", ["agents", "inbox"])
async def test_only_get_is_allowed(name: str) -> None:
    request = AuthedAsyncRequestFactory().post("/")

    response = await _route(urls.forward, name)(request)

    assert response.status_code == 405
