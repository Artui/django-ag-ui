from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness.sub_agents import SubAgent, SubAgents, TaskControl

from django_ag_ui import SUBAGENT_EVENT_NAME, SubAgentObserver
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_events_view import _body, _drain, _events, _factory, _route, _server
from tests.agent.test_subagent_task_control import _call

Step = Callable[[list[ModelMessage]], Awaitable[Any]]


def _model(*steps: Step) -> FunctionModel:
    remaining = list(steps)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        step = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        yield await step(messages)

    return FunctionModel(stream_function=stream)


def _said(value: Any) -> Step:
    async def step(messages: list[ModelMessage]) -> Any:
        return value

    return step


class _Run:
    """One detached, task-controlled run on a mount with a run store, driven by a test."""

    def __init__(self, child: Agent[Any, Any], *parent: Step, **server: Any) -> None:
        delegate = _call("delegate_task", {"agent_name": "researcher", "task": "Go."}, "call-1")
        sub_agents = SubAgents(
            agents=[SubAgent(child)], agent_folders=None, task_control=TaskControl()
        )
        self.server = _server(
            _model(_said(delegate), *parent),
            DefaultRunStore(),
            capabilities=[SubAgentObserver(sub_agents)],
            **server,
        )
        self.body: asyncio.Future[str] | None = None

    async def start(self) -> None:
        request = _factory().post("/agent/", data=_body("r1"), content_type="application/json")
        response = await _route(self.server, "endpoint")(request)
        self.body = asyncio.ensure_future(_drain(response))

    async def get(self) -> tuple[int, dict[str, Any]]:
        response = await _route(self.server, "thread_tasks")(
            _factory().get("/agent/threads/t1/tasks/"), thread_id="t1", action="list"
        )
        return response.status_code, json.loads(response.content)

    async def post(self, task_id: str, action: str, body: Any) -> tuple[int, dict[str, Any]]:
        data = body if isinstance(body, bytes) else json.dumps(body)
        request = _factory().post("/", data=data, content_type="application/json")
        view = _route(self.server, f"thread_task_{action}")
        response = await view(request, thread_id="t1", task_id=task_id, action=action)
        return response.status_code, json.loads(response.content)

    async def task(self, *, asking: bool = False) -> dict[str, Any]:
        for _ in range(500):
            status, body = await self.get()
            tasks = body.get("tasks", []) if status == 200 else []
            if tasks and (not asking or tasks[0]["question"] is not None):
                return tasks[0]
            await asyncio.sleep(0.01)
        raise AssertionError("no task came up")

    async def steps(self) -> list[dict[str, Any]]:
        assert self.body is not None
        events = _events(await asyncio.wait_for(self.body, 10))
        return [e for e in events if e.get("name") == SUBAGENT_EVENT_NAME or "code" in e]


def _gated_child(gate: asyncio.Event, seen: list[str]) -> Agent[Any, Any]:
    async def read_handbook(phrase: str) -> str:
        """Look a phrase up in the handbook."""
        await gate.wait()
        return "Three passages."

    async def answer(messages: list[ModelMessage]) -> Any:
        seen.append(str(messages))
        return "Found them."

    return Agent(
        _model(_said(_call("read_handbook", {"phrase": "settlement"}, "sub-1")), answer),
        name="researcher",
        description="Looks phrases up.",
        toolsets=[FunctionToolset([read_handbook])],
    )


def test_the_task_routes_mount_beside_cancel_only_with_a_run_store() -> None:
    patterns, _, _ = _server(TestModel(), DefaultRunStore()).urls
    routes = {p.name: str(p.pattern) for p in patterns}
    plain, _, _ = _server(TestModel()).urls

    assert routes["thread_tasks"] == "threads/<str:thread_id>/tasks/"
    for action in ("steer", "answer", "cancel"):
        assert routes[f"thread_task_{action}"] == (
            f"threads/<str:thread_id>/tasks/<str:task_id>/{action}/"
        )
    assert not {p.name for p in plain} & {"thread_tasks", "thread_task_steer"}


@pytest.mark.django_db(transaction=True)
async def test_a_user_steers_a_live_task_and_the_stream_says_so() -> None:
    gate, seen = asyncio.Event(), []
    run = _Run(_gated_child(gate, seen), _said("Done."))
    await run.start()
    task = await run.task()

    status, body = await run.post(task["task_id"], "steer", {"message": "Only 2024."})
    gate.set()
    steps = await run.steps()

    assert (status, body) == (202, {"thread_id": "t1", "task_id": task["task_id"], "steered": True})
    assert task["delegation_id"] == "call-1"
    assert (task["agent"], task["status"], task["question"]) == ("researcher", "running", None)
    steered = next(s["value"] for s in steps if s.get("value", {}).get("phase") == "steered")
    assert steered["status"] == "researcher: steered by you"
    assert steered["task"] == {"taskId": task["task_id"], "message": "Only 2024.", "source": "user"}
    assert "Only 2024." in seen[0]


@pytest.mark.django_db(transaction=True)
async def test_a_user_answers_a_tasks_question_by_its_id() -> None:
    answered = asyncio.Event()

    async def ask(messages: list[ModelMessage]) -> Any:
        return _call("ask_parent", {"question": "Which year?"}, "sub-q")

    async def wait_for_user(messages: list[ModelMessage]) -> Any:
        await answered.wait()
        return "Noted."

    child = Agent(_model(ask, _said("2024 it is.")), name="researcher", description="Asks.")
    run = _Run(child, wait_for_user, _said("Done."))
    await run.start()
    task = await run.task(asking=True)
    question = task["question"]

    wrong = await run.post(task["task_id"], "answer", {"question_id": "nope", "answer": "2024"})
    status, _ = await run.post(
        task["task_id"], "answer", {"question_id": question["question_id"], "answer": "2024"}
    )
    answered.set()
    steps = await run.steps()

    assert question["question"] == "Which year?"
    assert (wrong[0], wrong[1]["reason"]) == (409, "not_waiting")
    assert status == 202
    users = [s["value"]["task"] for s in steps if s.get("value", {}).get("phase") == "answered"]
    assert users == [
        {
            "taskId": task["task_id"],
            "questionId": question["question_id"],
            "answer": "2024",
            "source": "user",
        }
    ]


@pytest.mark.django_db(transaction=True)
async def test_a_forced_cancel_closes_the_task_as_cancelled() -> None:
    gate = asyncio.Event()
    run = _Run(_gated_child(gate, []), _said("Stopped."))
    await run.start()
    task = await run.task()

    status, body = await run.post(task["task_id"], "cancel", {"force": True})
    steps = await run.steps()

    assert (status, body["cancel_requested"], body["forced"]) == (202, True, True)
    asked = next(s["value"] for s in steps if s.get("value", {}).get("phase") == "cancel_requested")
    assert asked["task"] == {"taskId": task["task_id"], "forced": True, "source": "user"}
    assert [s["code"] for s in steps if "code" in s] == ["cancelled"]
    assert (await run.post(task["task_id"], "steer", {"message": "x"}))[1]["reason"] == "no_run"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"not json", "the body is not JSON"),
        ([], "the body is not a JSON object"),
        ({"message": "  "}, "message must be a non-blank string"),
        ({"message": "x" * 8001}, "message is longer than 8000 characters"),
        ({"force": "yes"}, "force must be a boolean"),
    ],
)
async def test_a_malformed_body_is_refused_before_the_task_is_touched(
    body: Any, error: str
) -> None:
    gate = asyncio.Event()
    run = _Run(_gated_child(gate, []), _said("Done."))
    await run.start()
    task = await run.task()
    action = "cancel" if "force" in error else "steer"

    status, refused = await run.post(task["task_id"], action, body)
    gate.set()
    await run.steps()

    assert (status, refused) == (
        400,
        {"error": error, "reason": "invalid", "thread_id": "t1", "task_id": task["task_id"]},
    )


@pytest.mark.django_db(transaction=True)
async def test_a_thread_with_no_run_and_a_run_not_live_here_answer_404() -> None:
    run_store = DefaultRunStore()
    server = _server(TestModel(), run_store)
    tasks = _route(server, "thread_tasks")

    idle = await tasks(_factory().get("/"), thread_id="t1", action="list")
    request = _factory().get("/")
    await run_store.claim("t1", "r1", request=request, lease=timedelta(minutes=5))
    elsewhere = await tasks(request, thread_id="t1", action="list")

    assert (idle.status_code, json.loads(idle.content)["reason"]) == (404, "no_run")
    assert json.loads(elsewhere.content) == {
        "error": "the thread's run is not live here",
        "reason": "not_live_here",
        "thread_id": "t1",
    }


@pytest.mark.django_db(transaction=True)
async def test_with_live_reattach_off_no_run_is_live_here() -> None:
    run_store = DefaultRunStore()
    config = build_ag_ui_config(heartbeat_seconds=0, live_reattach=False)
    server = _server(TestModel(), run_store, config=config)
    request = _factory().get("/")
    await run_store.claim("t1", "r1", request=request, lease=timedelta(minutes=5))

    response = await _route(server, "thread_tasks")(request, thread_id="t1", action="list")

    assert json.loads(response.content)["reason"] == "not_live_here"


async def test_the_wrong_method_and_a_denied_caller_are_refused() -> None:
    server = _server(TestModel(), DefaultRunStore())
    post = _factory().post("/", data="{}", content_type="application/json")
    anonymous = AsyncRequestFactory().get("/")
    anonymous.user = AnonymousUser()

    listed = await _route(server, "thread_tasks")(post, thread_id="t1", action="list")
    steer = await _route(server, "thread_task_steer")(
        _factory().get("/"), thread_id="t1", task_id="x", action="steer"
    )
    denied = await _route(server, "thread_tasks")(anonymous, thread_id="t1", action="list")

    assert (listed.status_code, listed["Allow"]) == (405, "GET")
    assert (steer.status_code, steer["Allow"]) == (405, "POST")
    assert denied.status_code in (401, 403)


@pytest.mark.django_db(transaction=True)
async def test_an_anonymous_caller_on_an_open_mount_has_no_owner_scope() -> None:
    server = AGUIServer(
        ToolRegistry(),
        model=TestModel(),
        csrf_exempt=True,
        require_authenticated=False,
        run_store=DefaultRunStore(),
    )
    anonymous = AsyncRequestFactory().get("/")
    anonymous.user = AnonymousUser()

    response = await _route(server, "thread_tasks")(anonymous, thread_id="t1", action="list")

    assert response.status_code == 403
