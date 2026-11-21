from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from django.http import StreamingHttpResponse
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore

import django_ag_ui
from django_ag_ui import persistence
from django_ag_ui.persistence import threads_view as threads_view_module
from django_ag_ui.persistence.types.mount_identity import MountIdentity
from tests.authed_request_factory import AuthedRequestFactory
from tests.persistence.test_thread_status import _SSE, _list, _route, _server, _thread

pytestmark = pytest.mark.django_db(transaction=True)


async def _wsgi_list(server: Any, **headers: str) -> Any:
    request = AuthedRequestFactory().get("/agent/threads/", headers=headers)
    return await _route(server, "threads")(request)


async def test_a_wsgi_request_asking_for_a_stream_is_answered_as_a_poll() -> None:
    await _thread("t1")

    response = await _wsgi_list(_server(DefaultRunStore()), **_SSE)

    assert not isinstance(response, StreamingHttpResponse)
    assert response["Content-Type"] == "application/json"
    assert [row["thread_id"] for row in json.loads(response.content)["threads"]] == ["t1"]
    assert response["ETag"]


@pytest.mark.parametrize("heartbeat", [0, 0.01])
async def test_the_stream_ends_at_its_lifetime_cap(
    monkeypatch: pytest.MonkeyPatch, heartbeat: float
) -> None:
    monkeypatch.setattr(threads_view_module, "_STREAM_POLL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(threads_view_module, "_STREAM_MAX_SECONDS", 0.1, raising=False)
    await _thread("t1")
    server = _server(DefaultRunStore(), heartbeat)

    response = await _list(server, **_SSE)
    chunks = await asyncio.wait_for(_drain(response.streaming_content), timeout=5)

    assert chunks[0].startswith(b"id: ")


async def _drain(stream: Any) -> list[bytes]:
    return [chunk async for chunk in stream]


async def test_both_answers_vary_on_accept() -> None:
    await _thread("t1")
    server = _server(DefaultRunStore())

    poll = await _list(server)
    stream = await _list(server, **_SSE)
    await stream.streaming_content.aclose()

    assert poll["Vary"] == "Accept"
    assert stream["Vary"] == "Accept"


async def test_the_wsgi_poll_answer_varies_on_accept_too() -> None:
    await _thread("t1")

    response = await _wsgi_list(_server(DefaultRunStore()), **_SSE)

    assert response["Vary"] == "Accept"


@pytest.mark.parametrize(
    "header",
    ["W/{etag}", '"other", {etag}', 'W/"other", W/{etag}', "*"],
    ids=["weak", "list", "weak-list", "wildcard"],
)
async def test_a_revalidation_matches_weakly_and_across_a_list(header: str) -> None:
    await _thread("t1")
    server = _server(DefaultRunStore())
    etag = (await _list(server))["ETag"]

    response = await _list(server, **{"If-None-Match": header.format(etag=etag)})

    assert response.status_code == 304
    assert response["ETag"] == etag


@pytest.mark.parametrize("header", ['"other"', 'W/"other", "another"', "garbage"])
async def test_a_revalidation_naming_no_current_etag_gets_the_body(header: str) -> None:
    await _thread("t1")

    response = await _list(_server(DefaultRunStore()), **{"If-None-Match": header})

    assert response.status_code == 200


async def test_a_mount_with_only_a_description_carries_the_agent_key() -> None:
    await _thread("t1")

    body = json.loads((await _list(_server(DefaultRunStore(), description="Billing"))).content)

    assert body["agent"] == {"id": None, "name": None, "description": "Billing"}


def test_the_mount_identity_is_exported_where_the_views_argument_names_it() -> None:
    assert persistence.MountIdentity is MountIdentity
    assert django_ag_ui.MountIdentity is MountIdentity
    assert "MountIdentity" in django_ag_ui.__all__
