from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.replay_head import replay_head
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_events_view import _body, _drain, _factory, _Gate, _route, _settled, _started
from tests.agent.test_replay_head import SUBAGENT_RUN, log_of

_HARNESS = Path(__file__).parent.parent / "fixtures" / "replay_verify"
_USER = {"id": "u-r1", "role": "user", "content": "hi"}

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (_HARNESS / "node_modules").is_dir(),
    reason="needs node and `npm ci` in tests/fixtures/replay_verify (not run in CI)",
)


def _scenario() -> tuple[ToolRegistry, FunctionModel]:
    registry = ToolRegistry()

    @tool(registry)
    def lookup(city: str) -> str:
        """Look a city up."""
        return f"{city} is sunny"

    turns = {"n": 0}

    async def stream_fn(messages: list, info: Any) -> Any:
        turns["n"] += 1
        if turns["n"] == 1:
            yield {0: DeltaThinkingPart(content="which city")}
            yield {1: DeltaThinkingPart(content=" is meant")}
            yield "Checking the weather"
            yield {2: DeltaToolCall(name="lookup", json_args='{"city":', tool_call_id="c1")}
            yield {2: DeltaToolCall(json_args=' "Oslo"}')}
        else:
            yield "Oslo is sunny; "
            yield "shall I book?"
            yield {1: DeltaToolCall(name="confirm", json_args='{"what":', tool_call_id="c2")}
            yield {1: DeltaToolCall(json_args=' "a trip"}')}

    return registry, FunctionModel(stream_function=stream_fn)


def _with_client_tool(body: bytes) -> bytes:
    data = json.loads(body)
    schema = {"type": "object", "properties": {"what": {"type": "string"}}}
    data["tools"] = [{"name": "confirm", "description": "Ask first.", "parameters": schema}]
    return json.dumps(data).encode()


def _server(registry: ToolRegistry, model: Any, run_store: DefaultRunStore) -> AGUIServer:
    return AGUIServer(
        registry,
        model=model,
        csrf_exempt=True,
        conversation_store=DefaultConversationStore(),
        run_store=run_store,
        config=build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300),
    )


async def _replay(server: AGUIServer, after: int) -> str:
    request = _factory().get(f"/agent/threads/t1/events/?after={after}", headers=_SSE)
    return await _drain(await _route(server, "thread_events")(request, thread_id="t1"))


_SSE = {"Accept": "text/event-stream"}


def _verify(cases: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    source, verdict = tmp_path / "cases.json", tmp_path / "verdict.json"
    source.write_text(json.dumps({"cases": cases}))
    subprocess.run(["node", "verify.mjs", str(source), str(verdict)], cwd=_HARNESS, check=True)
    return json.loads(verdict.read_text())


@pytest.mark.django_db(transaction=True)
async def test_a_replay_from_every_seq_passes_the_real_client(tmp_path: Path) -> None:
    run_store = DefaultRunStore()
    server = _server(*_scenario(), run_store)
    body = _with_client_tool(_body("r1"))
    request = _factory().post("/agent/", data=body, content_type="application/json")
    live = await _drain(await _route(server, "endpoint")(request))
    await _settled(run_store, request)
    log = await run_store.read("r1", request=request)
    cases: dict[str, Any] = {"live": {"streams": [live], "messages": [_USER]}}
    for after in range(len(log) + 1):
        replay = await _replay(server, after)
        cases[f"fresh-{after}"] = {"streams": [replay], "messages": []}
        seen = "".join(f"data: {json.dumps(e.event)}\n\n" for e in log[:after])
        cases[f"reconnect-{after}"] = {"streams": [seen, replay], "messages": [_USER]}

    verdict = _verify(cases, tmp_path)

    results = verdict["results"]
    assert verdict["client"] == "1.0.4"
    assert {name: r.get("error") for name, r in results.items() if not r["ok"]} == {}
    expected = results["live"]["messages"]
    roles = [m["role"] for m in expected]
    assert roles == ["user", "reasoning", "reasoning", "assistant", "tool", "assistant"]
    for name, result in results.items():
        assert result["messages"] == expected, name


@pytest.mark.django_db(transaction=True)
async def test_a_replay_opened_mid_run_tails_it_to_the_end(tmp_path: Path) -> None:
    gate, run_store = _Gate(), DefaultRunStore()
    server = _server(ToolRegistry(), gate.model(), run_store)
    request = _factory().post("/agent/", data=_body("r1"), content_type="application/json")
    stream = await _started(await _route(server, "endpoint")(request))
    for _ in range(300):
        if len(await run_store.read("r1", request=request)) >= 2:
            break
        await asyncio.sleep(0.01)
    replays = [asyncio.ensure_future(_replay(server, after)) for after in (2, 99)]
    await asyncio.sleep(0.05)
    gate.release.set()
    async for _chunk in stream:
        pass

    cases = {f"midrun-{i}": {"streams": [await r], "messages": []} for i, r in enumerate(replays)}

    results = _verify(cases, tmp_path)["results"]

    for result in results.values():
        assert result["ok"], result.get("error")
        assert [m.get("content") for m in result["messages"]] == ["partial answer and the rest"]


def _sse(events: list[dict[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def test_a_subagents_replay_from_every_seq_passes_the_real_client(tmp_path: Path) -> None:
    log = log_of(SUBAGENT_RUN)
    cases: dict[str, Any] = {"live": {"streams": [_sse(SUBAGENT_RUN)], "messages": []}}
    for after in range(len(log) + 1):
        head, cut = replay_head(log, [], after=after)
        replay = _sse([*head, *(e.event for e in log[cut:])])
        cases[f"fresh-{after}"] = {"streams": [replay], "messages": []}
        cases[f"reconnect-{after}"] = {"streams": [_sse(SUBAGENT_RUN[:after]), replay]}

    results = _verify(cases, tmp_path)["results"]

    assert {name: r.get("error") for name, r in results.items() if not r["ok"]} == {}
    for name, result in results.items():
        assert result["messages"] == results["live"]["messages"], name
