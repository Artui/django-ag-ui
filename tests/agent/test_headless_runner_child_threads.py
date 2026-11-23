from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_spec_agents.contrib.store.default_child_session_index import DefaultChildSessionIndex
from django_spec_agents.contrib.store.default_conversation_store import DefaultConversationStore
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore
from django_spec_agents.persistence.scoped_conversation_store import ScopedConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_waiting import RunWaiting
from django_spec_agents.persistence.types.thread_ref import ThreadRef
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import AgentInfo, FunctionModel

import django_ag_ui.agent.run_owner as run_owner_module
from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedAsyncRequestFactory, authenticated_user


class _Parks:
    """Answers once, then parks until cancelled; counts the runs it saw."""

    def __init__(self) -> None:
        self.seen = 0

    def build(self) -> FunctionModel:
        async def stream_fn(messages: list, info: AgentInfo) -> Any:
            self.seen += 1
            yield "working"
            await asyncio.Event().wait()

        return FunctionModel(stream_function=stream_fn)


class _NoUnlink:
    """A conversation store that cannot clear a parent link."""

    def __init__(self) -> None:
        self._inner = DefaultConversationStore()

    def __getattr__(self, name: str) -> Any:
        if name == "unlink_parent":
            raise AttributeError(name)
        return getattr(self._inner, name)


class _Late:
    runner: AGUIHeadlessRunner


def _mount(agent_id: str, store: ConversationStore, model: _Parks, **extra: Any) -> AGUIServer:
    return AGUIServer(
        ToolRegistry(),
        model=model.build(),
        csrf_exempt=True,
        conversation_store=store,
        step_store=DefaultStepStore,
        run_store=DefaultRunStore(),
        config=build_ag_ui_config(heartbeat_seconds=0),
        id=agent_id,
        **extra,
    )


def _scoped(late: _Late, model: _Parks) -> tuple[AGUIServer, AGUIServer]:
    store = DefaultConversationStore()
    a = _mount(
        "a",
        ScopedConversationStore(store, scope="a"),
        model,
        child_threads=lambda: late.runner,
    )
    b = _mount("b", ScopedConversationStore(store, scope="b"), model)
    late.runner = AGUIHeadlessRunner(a, b, index=DefaultChildSessionIndex(DefaultRunStore()))
    return a, b


async def _seed(server: AGUIServer, thread_id: str, parent: ThreadRef | None, pk: int = 1) -> None:
    user = authenticated_user(pk=pk)
    store = server._conversation_store
    conversation = Conversation(thread_id=thread_id, messages=[], owner_id=str(pk), parent=parent)
    await store.save(conversation, request=server.headless_request(user))


async def _call(server: AGUIServer, method: str, path: str, **kwargs: Any) -> Any:
    view = server._threads_view
    assert view is not None
    request = getattr(AuthedAsyncRequestFactory(), method)(path)
    return await view(request, **kwargs)


async def _rows(server: AGUIServer) -> dict[str, dict[str, Any]]:
    response = await _call(server, "get", "/threads/")
    return {row["thread_id"]: row for row in json.loads(response.content)["threads"]}


async def _until(predicate: Any) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


@pytest.mark.django_db(transaction=True)
async def test_deleting_a_parent_cancels_its_running_tree_unlinks_its_children_and_keeps_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_owner_module, "_WATCH_SECONDS", 0.01)
    late, model = _Late(), _Parks()
    a, b = _scoped(late, model)
    user = authenticated_user()
    cancelled: list[str] = []
    request_cancel = b.request_cancel

    async def spy(thread_id: str, *, request: HttpRequest) -> bool:
        cancelled.append(thread_id)
        return await request_cancel(thread_id, request=request)

    monkeypatch.setattr(b, "request_cancel", spy)
    await _seed(a, "p", None)
    await _seed(b, "idle", ThreadRef("p", "a"))
    child = asyncio.ensure_future(
        late.runner.run_to_first_stop(
            "b", "go", user, timeout=5, thread_id="c", parent=ThreadRef("p", "a")
        )
    )
    await _until(lambda: model.seen == 1)
    grandchild = asyncio.ensure_future(
        late.runner.run_to_first_stop(
            "b", "go", user, timeout=5, thread_id="g", parent=ThreadRef("c", "b")
        )
    )
    await _until(lambda: model.seen == 2)

    response = await _call(a, "delete", "/threads/p/", thread_id="p")

    assert response.status_code == 204
    stops = await asyncio.wait_for(asyncio.gather(child, grandchild), timeout=5)
    assert [stop.outcome for stop in stops] == [RunOutcome.CANCELLED] * 2
    assert sorted(cancelled) == ["c", "g"]
    rows = await _rows(b)
    assert (rows["c"]["parent"], rows["idle"]["parent"]) == (None, None)
    assert rows["g"]["parent"] == {"thread_id": "c", "scope": "b"}
    assert "p" not in await _rows(a)


@pytest.mark.django_db(transaction=True)
async def test_a_parked_child_keeps_its_park_and_loses_only_its_parent() -> None:
    late, model = _Late(), _Parks()
    a, b = _scoped(late, model)
    await _seed(a, "p", None)
    await _seed(b, "w", ThreadRef("p", "a"))
    request = b.headless_request(authenticated_user())
    await b._run_store.claim("w", "r1", request=request, lease=timedelta(minutes=5))
    waiting = (
        RunWaiting(kind="question", summary="Which one?", tool_call_id="q-1", destructive=False),
    )
    await b._run_store.finish("w", "r1", RunOutcome.WAITING, request=request, waiting=waiting)
    before = (await _rows(b))["w"]

    response = await _call(a, "delete", "/threads/p/", thread_id="p")

    after = (await _rows(b))["w"]
    assert response.status_code == 204
    assert after == before | {"parent": None}
    assert after["status"] == "waiting"


@pytest.mark.django_db(transaction=True)
async def test_the_children_route_lists_the_callers_children_in_every_scope() -> None:
    late, model = _Late(), _Parks()
    a, b = _scoped(late, model)
    await _seed(a, "p", None)
    await _seed(a, "here", ThreadRef("p", "a"))
    await _seed(b, "there", ThreadRef("p", "a"))
    await _seed(b, "theirs", ThreadRef("p", "a"), pk=2)

    response = await _call(a, "get", "/threads/p/children/", thread_id="p", action="children")

    assert response.status_code == 200
    assert json.loads(response.content) == {
        "children": [
            {"thread_id": "here", "agent_id": "a", "status": "idle"},
            {"thread_id": "there", "agent_id": "b", "status": "idle"},
        ]
    }


@pytest.mark.django_db(transaction=True)
async def test_the_children_of_a_thread_the_caller_does_not_own_are_not_found() -> None:
    late, model = _Late(), _Parks()
    a, _ = _scoped(late, model)
    await _seed(a, "p", None, pk=2)

    response = await _call(a, "get", "/threads/p/children/", thread_id="p", action="children")

    assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_the_children_route_answers_only_a_get() -> None:
    late, model = _Late(), _Parks()
    a, _ = _scoped(late, model)
    await _seed(a, "p", None)

    response = await _call(a, "post", "/threads/p/children/", thread_id="p", action="children")

    assert response.status_code == 405


@pytest.mark.django_db(transaction=True)
async def test_a_child_in_an_unscoped_store_is_unlinked_through_its_parents_mount() -> None:
    late, model = _Late(), _Parks()
    a = _mount("a", DefaultConversationStore(), model, child_threads=lambda: late.runner)
    late.runner = AGUIHeadlessRunner(a, index=DefaultChildSessionIndex(DefaultRunStore()))
    await _seed(a, "p", None)
    await _seed(a, "c", ThreadRef("p", "a"))

    response = await _call(a, "delete", "/threads/p/", thread_id="p")

    assert response.status_code == 204
    assert list(await _rows(a)) == ["c"]
    assert (await _rows(a))["c"]["parent"] is None


@pytest.mark.django_db(transaction=True)
async def test_a_child_in_a_scope_no_mount_serves_stops_the_delete_before_it_changes_anything() -> (
    None
):
    late, model = _Late(), _Parks()
    a, b = _scoped(late, model)
    await _seed(a, "p", None)
    await _seed(b, "c", ThreadRef("p", "a"))
    elsewhere = _mount("z", ScopedConversationStore(DefaultConversationStore(), scope="z"), model)
    await _seed(elsewhere, "lost", ThreadRef("p", "a"))

    with pytest.raises(LookupError, match="'z'"):
        await late.runner.delete_thread("a", "p", authenticated_user())

    assert (await _rows(b))["c"]["parent"] == {"thread_id": "p", "scope": "a"}
    assert "p" in await _rows(a)


@pytest.mark.django_db(transaction=True)
async def test_a_delete_through_the_view_answers_409_when_a_child_scope_has_no_mount() -> None:
    late, model = _Late(), _Parks()
    a, b = _scoped(late, model)
    await _seed(a, "p", None)
    elsewhere = _mount("z", ScopedConversationStore(DefaultConversationStore(), scope="z"), model)
    await _seed(elsewhere, "lost", ThreadRef("p", "a"))
    request = AuthedAsyncRequestFactory().delete("/a/threads/p/")

    response = await a._threads_view(request, thread_id="p")

    assert response.status_code == 409
    assert "'z'" in json.loads(response.content)["error"]
    assert "p" in await _rows(a)


def test_a_runner_with_an_index_refuses_a_mount_that_cannot_unlink() -> None:
    model = _Parks()
    with pytest.raises(ImproperlyConfigured, match="'b'.*unlink_parent"):
        AGUIHeadlessRunner(
            _mount("a", DefaultConversationStore(), model),
            _mount("b", ScopedConversationStore(_NoUnlink(), scope="b"), model),
            index=DefaultChildSessionIndex(DefaultRunStore()),
        )


def test_a_runner_without_an_index_keeps_a_mount_that_cannot_unlink() -> None:
    AGUIHeadlessRunner(_mount("b", ScopedConversationStore(_NoUnlink(), scope="b"), _Parks()))


@pytest.mark.parametrize("call", ["children", "delete_thread"])
async def test_a_runner_without_an_index_answers_no_child_question(call: str) -> None:
    runner = AGUIHeadlessRunner(_mount("a", DefaultConversationStore(), _Parks()))
    with pytest.raises(ImproperlyConfigured, match="index="):
        await getattr(runner, call)("a", "p", authenticated_user())


def test_child_threads_without_an_id_is_refused_at_startup() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"child_threads=\) needs id="):
        _mount(None, DefaultConversationStore(), _Parks(), child_threads=lambda: None)


def test_the_children_route_is_mounted_only_with_child_threads() -> None:
    late, model = _Late(), _Parks()
    with_children, _ = _scoped(late, model)
    without = _mount("a", DefaultConversationStore(), model)
    assert "thread_children" in with_children.routes()
    assert "thread_children" not in without.routes()
