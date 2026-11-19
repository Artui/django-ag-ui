from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore

from tests.agent.test_queued_browser_start import _body, _drain, _Model, _post, _queued, _route
from tests.agent.test_replay_against_client import _SSE, _verify, pytestmark  # noqa: F401
from tests.authed_request_factory import AuthedAsyncRequestFactory


async def _replay(server: Any, after: int) -> str:
    request = AuthedAsyncRequestFactory().get(
        f"/agent/threads/t1/events/?after={after}", headers=_SSE
    )
    return await _drain(await _route(server, "thread_events")(request, thread_id="t1"))


@pytest.mark.django_db(transaction=True)
async def test_a_queued_runs_replay_from_every_seq_passes_the_real_client(tmp_path: Path) -> None:
    server, queue = _queued(_Model())
    await _route(server, "endpoint")(_post(_body("r1")))
    await queue.work()
    log = await DefaultRunStore().read("r1", request=_post(b"{}"))
    cases = {
        f"fresh-{after}": {"streams": [await _replay(server, after)], "messages": []}
        for after in range(len(log) + 1)
    }

    results = _verify(cases, tmp_path)["results"]

    assert log[0].event["type"] == "CUSTOM"
    assert {name: r.get("error") for name, r in results.items() if not r["ok"]} == {}
    for name, result in results.items():
        contents = [m.get("content") for m in result["messages"]]
        assert contents == ["paint it", "queued answer"], name
