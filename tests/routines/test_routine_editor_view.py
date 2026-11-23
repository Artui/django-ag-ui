from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from django_service_specs import NotPermitted
from django_spec_agents import ToolGuardConfig
from django_spec_agents.headless.types.firing_state import FiringState
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_agents.routines import MAX_ROUTINES_PER_USER, RoutineSweep
from pydantic_ai.models.function import FunctionModel

from django_ag_ui import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.routineapp.models import TeamRoutine
from tests.routineapp.people import WEEKDAYS_AT_8, FakeQueue, person, request_as, routine

pytestmark = pytest.mark.django_db

_IDLE = FunctionModel(lambda messages, info: None)
_DRAFT = {
    "name": "triage",
    "prompt": "Sort the inbox.",
    "schedule": WEEKDAYS_AT_8,
    "timezone": "Europe/Paris",
}


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    @tool(registry)
    def look(query: str) -> str:
        return query

    @tool(registry, destructive=True)
    def drop_table(name: str) -> str:
        return name

    return registry


def _routes(**options: Any) -> dict[str, Any]:
    options.setdefault("id", "helper")
    server = AGUIServer(_registry(), model=_IDLE, routine_model=TeamRoutine, **options)
    patterns, _, _ = server.urls
    return {pattern.name: (pattern.callback, pattern.default_args) for pattern in patterns}


def _call(name: str, user: Any, method: str = "get", body: Any = None, **kwargs: Any) -> Any:
    view, defaults = _routes()[name]
    return view(request_as(user, method, body), **defaults, **kwargs)


def _json(response: Any) -> Any:
    return json.loads(response.content)


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr(TeamRoutine, "sweep", RoutineSweep(queue=fake, spec_name="fire"))
    return fake


def test_the_list_serves_the_callers_own_writable_routines_on_this_mount() -> None:
    ann, bob = person("ann"), person("bob")
    mine = routine(ann, "standup")
    routine(ann, "elsewhere", agent_id="other")
    routine(ann, "locked-report")
    routine(bob, "teammate")
    body = _json(_call("routines", ann))
    assert [entry["id"] for entry in body["routines"]] == [str(mine.pk)]
    assert body["routines"][0] | {"nextFireAt": None} == {
        "id": str(mine.pk),
        "name": "standup",
        "agentId": "helper",
        "prompt": "Run standup.",
        "schedule": WEEKDAYS_AT_8,
        "timezone": "Europe/Paris",
        "enabled": True,
        "pausedReason": "",
        "nextFireAt": None,
        "preapprovedTools": [],
        "lastRunId": None,
        "lastThreadId": None,
    }
    assert body["grantable"] == [
        {"name": "look", "destructive": False},
        {"name": "drop_table", "destructive": True},
    ]


def test_create_runs_on_the_mounts_agent_as_the_caller() -> None:
    ann = person("ann")
    response = _call("routines", ann, "post", {**_DRAFT, "agentId": "other"})
    assert response.status_code == 201
    created = TeamRoutine.objects.get(name="triage")
    assert (created.agent_id, created.principal, created.team) == ("helper", ann, "ops")
    assert created.next_fire_at is not None
    assert _json(response)["id"] == str(created.pk)


def test_a_destructive_grant_without_its_confirmation_is_refused_and_nothing_is_saved() -> None:
    ann = person("ann")
    body = {**_DRAFT, "preapprovedTools": ["look", "drop_table"]}
    response = _call("routines", ann, "post", body)
    assert response.status_code == 409
    assert _json(response)["destructive"] == ["drop_table"]
    assert not TeamRoutine.objects.exists()


def test_a_destructive_grant_confirmed_by_name_is_saved_with_its_flag() -> None:
    ann = person("ann")
    body = {**_DRAFT, "preapprovedTools": ["look", "drop_table"]}
    response = _call("routines", ann, "post", {**body, "confirmDestructive": ["drop_table"]})
    assert response.status_code == 201
    assert _json(response)["preapprovedTools"] == [
        {"name": "look", "destructive": False},
        {"name": "drop_table", "destructive": True},
    ]


def test_an_update_keeping_a_destructive_grant_names_it_again() -> None:
    ann = person("ann")
    mine = routine(ann, preapproved_tools=[{"name": "drop_table", "destructive": True}])
    body = {**_DRAFT, "preapprovedTools": ["drop_table"]}
    refused = _call("routine", ann, "put", body, routine_id=str(mine.pk))
    assert refused.status_code == 409
    mine.refresh_from_db()
    assert mine.name == "standup"
    confirmed = {**body, "confirmDestructive": ["drop_table"]}
    assert _call("routine", ann, "put", confirmed, routine_id=str(mine.pk)).status_code == 200
    mine.refresh_from_db()
    assert mine.name == "triage"


def test_an_update_keeps_the_flag_pinned_when_a_tool_was_granted() -> None:
    ann = person("ann")
    mine = routine(ann, preapproved_tools=[{"name": "drop_table", "destructive": False}])
    body = {**_DRAFT, "preapprovedTools": ["drop_table"]}
    response = _call("routine", ann, "put", body, routine_id=str(mine.pk))
    assert response.status_code == 200
    assert _json(response)["preapprovedTools"] == [{"name": "drop_table", "destructive": False}]


@pytest.mark.parametrize("who", ["teammate", "locked", "other-mount", "missing"])
@pytest.mark.parametrize(
    ("name", "method", "body"),
    [
        ("routine", "put", _DRAFT),
        ("routine", "patch", {"enabled": False}),
        ("routine_run", "post", {}),
        ("routine_runs", "get", None),
    ],
)
def test_a_routine_the_caller_may_not_write_here_is_a_404(
    who: str, name: str, method: str, body: Any
) -> None:
    ann, bob = person("ann"), person("bob")
    target = {
        "teammate": lambda: routine(bob, "theirs"),
        "locked": lambda: routine(ann, "locked-report"),
        "other-mount": lambda: routine(ann, "elsewhere", agent_id="other"),
        "missing": lambda: None,
    }[who]()
    routine_id = "999" if target is None else str(target.pk)
    response = _call(name, ann, method, body, routine_id=routine_id)
    assert response.status_code == 404
    if target is not None:
        target.refresh_from_db()
        assert (target.enabled, target.last_run_id) == (True, None)


def test_patch_pauses_and_resumes_keeping_the_grant() -> None:
    ann = person("ann")
    mine = routine(ann, preapproved_tools=[{"name": "drop_table", "destructive": True}])
    paused = _call("routine", ann, "patch", {"enabled": False}, routine_id=str(mine.pk))
    assert (_json(paused)["enabled"], _json(paused)["nextFireAt"]) == (False, None)
    resumed = _json(_call("routine", ann, "patch", {"enabled": True}, routine_id=str(mine.pk)))
    assert resumed["enabled"] is True and resumed["nextFireAt"] is not None
    assert resumed["preapprovedTools"] == [{"name": "drop_table", "destructive": True}]


_OFF_CATALOG = [{"name": "wipe_disk", "destructive": True}]


def test_a_grant_held_from_a_tool_the_editor_cannot_list_still_resumes() -> None:
    ann = person("ann")
    mine = routine(ann, enabled=False, preapproved_tools=_OFF_CATALOG)
    response = _call("routine", ann, "patch", {"enabled": True}, routine_id=str(mine.pk))
    assert response.status_code == 200
    assert _json(response)["preapprovedTools"] == _OFF_CATALOG


def test_a_grant_held_from_a_tool_the_editor_cannot_list_is_kept_by_a_confirmed_put() -> None:
    ann = person("ann")
    mine = routine(ann, preapproved_tools=_OFF_CATALOG)
    body = {**_DRAFT, "preapprovedTools": ["wipe_disk"]}
    refused = _call("routine", ann, "put", body, routine_id=str(mine.pk))
    assert _json(refused)["destructive"] == ["wipe_disk"]
    confirmed = {**body, "confirmDestructive": ["wipe_disk"]}
    response = _call("routine", ann, "put", confirmed, routine_id=str(mine.pk))
    assert response.status_code == 200
    assert _json(response)["preapprovedTools"] == _OFF_CATALOG


def test_a_put_adding_a_tool_the_mount_does_not_offer_is_a_400() -> None:
    ann = person("ann")
    mine = routine(ann, preapproved_tools=_OFF_CATALOG)
    body = {**_DRAFT, "preapprovedTools": ["wipe_disk", "format_disk"]}
    response = _call("routine", ann, "put", body, routine_id=str(mine.pk))
    assert response.status_code == 400
    assert "format_disk" in _json(response)["error"]


def test_run_now_enqueues_a_firing_into_a_fresh_thread(queue: FakeQueue) -> None:
    ann = person("ann")
    mine = routine(ann)
    response = _call("routine_run", ann, "post", {}, routine_id=str(mine.pk))
    assert response.status_code == 202
    body = _json(response)
    [(spec, params, actor)] = queue.enqueued
    assert (spec, params, actor) == (
        "fire",
        {"routine_id": mine.pk, "thread_id": body["threadId"]},
        ann,
    )
    mine.refresh_from_db()
    assert (mine.last_run_id, mine.last_thread_id) == (body["runId"], body["threadId"])
    runs = _json(_call("routine_runs", ann, routine_id=str(mine.pk)))
    assert runs == {"runs": [{"runId": "run-1", "threadId": body["threadId"], "state": None}]}
    queue.states["run-1"] = FiringState.FINISHED
    runs = _json(_call("routine_runs", ann, routine_id=str(mine.pk)))
    assert runs["runs"][0]["state"] == "finished"


def test_run_now_waits_for_a_pending_firing(queue: FakeQueue) -> None:
    ann = person("ann")
    mine = routine(ann, last_run_id="run-0")
    queue.states["run-0"] = FiringState.PENDING
    response = _call("routine_run", ann, "post", {}, routine_id=str(mine.pk))
    assert response.status_code == 409
    assert queue.enqueued == []


def test_run_now_waits_for_a_thread_still_waiting_on_a_person(
    queue: FakeQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    ann = person("ann")
    mine = routine(ann, last_run_id="run-0", last_thread_id="t0")
    queue.states["run-0"] = FiringState.FINISHED
    monkeypatch.setattr(TeamRoutine, "waiting", frozenset({"t0"}))
    response = _call("routine_run", ann, "post", {}, routine_id=str(mine.pk))
    assert response.status_code == 409
    assert queue.enqueued == []


def test_run_now_fires_after_a_finished_firing_whose_thread_is_done(queue: FakeQueue) -> None:
    ann = person("ann")
    mine = routine(ann, last_run_id="run-0", last_thread_id="t0")
    queue.states["run-0"] = FiringState.FINISHED
    response = _call("routine_run", ann, "post", {}, routine_id=str(mine.pk))
    assert response.status_code == 202


def test_run_now_refuses_a_routine_its_policy_may_not_run(queue: FakeQueue) -> None:
    ann = person("ann")
    mine = routine(ann, "grounded")
    response = _call("routine_run", ann, "post", {}, routine_id=str(mine.pk))
    assert response.status_code == 409
    assert queue.enqueued == []


def test_run_now_without_a_sweep_is_a_409_and_runs_list_the_last_firing() -> None:
    ann = person("ann")
    mine = routine(ann, last_run_id="run-0", last_thread_id="t0")
    assert _call("routine_run", ann, "post", {}, routine_id=str(mine.pk)).status_code == 409
    runs = _json(_call("routine_runs", ann, routine_id=str(mine.pk)))
    assert runs == {"runs": [{"runId": "run-0", "threadId": "t0", "state": None}]}


def test_run_now_reports_what_the_queue_refuses(queue: FakeQueue) -> None:
    ann = person("ann")
    mine = routine(ann)
    queue.refuse = NotPermitted("This routine may not fire as this user.")
    response = _call("routine_run", ann, "post", {}, routine_id=str(mine.pk))
    assert response.status_code == 403
    mine.refresh_from_db()
    assert mine.last_run_id is None


def test_a_routine_never_fired_has_no_runs() -> None:
    ann = person("ann")
    mine = routine(ann)
    assert _json(_call("routine_runs", ann, routine_id=str(mine.pk))) == {"runs": []}


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({**_DRAFT, "name": 3}, 400),
        ({**_DRAFT, "enabled": "yes"}, 400),
        ({**_DRAFT, "schedule": {"kind": "cron"}}, 400),
        ({**_DRAFT, "preapprovedTools": "look"}, 400),
        ({**_DRAFT, "preapprovedTools": [1]}, 400),
        ({**_DRAFT, "preapprovedTools": ["nope"]}, 400),
        ({**_DRAFT, "timezone": "Mars/Base"}, 400),
        ([], 400),
    ],
)
def test_a_broken_create_is_a_400(body: Any, status: int) -> None:
    response = _call("routines", person("ann"), "post", body)
    assert response.status_code == status
    assert "error" in _json(response)


def test_a_body_that_is_not_json_is_a_400() -> None:
    view, _ = _routes()["routines"]
    request = RequestFactory().post("/r/", data="{", content_type="application/json")
    request.user = person("ann")
    assert view(request).status_code == 400


def test_a_patch_without_a_boolean_is_a_400() -> None:
    ann = person("ann")
    mine = routine(ann)
    response = _call("routine", ann, "patch", {"enabled": "no"}, routine_id=str(mine.pk))
    assert response.status_code == 400


def test_a_create_the_policy_refuses_is_a_403() -> None:
    response = _call("routines", person("ann"), "post", {**_DRAFT, "name": "locked-one"})
    assert response.status_code == 403
    assert not TeamRoutine.objects.exists()


def test_a_write_that_is_not_json_is_a_415() -> None:
    view, _ = _routes()["routines"]
    request = RequestFactory().post("/r/", data="name=x", content_type="text/plain")
    request.user = person("ann")
    assert view(request).status_code == 415


def test_an_anonymous_caller_is_refused() -> None:
    from django.contrib.auth.models import AnonymousUser

    assert _call("routines", AnonymousUser()).status_code == 401


@pytest.mark.parametrize(
    ("name", "method", "allowed"),
    [
        ("routines", "delete", "GET, POST"),
        ("routine", "get", "PUT, PATCH"),
        ("routine_run", "get", "POST"),
        ("routine_runs", "post", "GET"),
    ],
)
def test_other_methods_are_a_405(name: str, method: str, allowed: str) -> None:
    ann = person("ann")
    mine = routine(ann)
    kwargs = {} if name == "routines" else {"routine_id": str(mine.pk)}
    body = {} if method in ("post", "delete") else None
    response = _call(name, ann, method, body, **kwargs)
    assert response.status_code == 405
    assert response["Allow"] == allowed


def test_the_editor_is_csrf_checked_unless_exempted_explicitly() -> None:
    assert _routes()["routines"][0].csrf_exempt is False
    assert _routes(csrf_exempt=True)["routines"][0].csrf_exempt is True


def test_a_mount_without_routines_serves_none() -> None:
    patterns, _, _ = AGUIServer(_registry(), model=_IDLE).urls
    assert not {"routines", "routine"} & {pattern.name for pattern in patterns}


def test_routines_need_the_mounts_id() -> None:
    with pytest.raises(ImproperlyConfigured, match="needs id="):
        AGUIServer(_registry(), model=_IDLE, routine_model=TeamRoutine)


def test_routines_need_a_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TeamRoutine, "routine_policy", classmethod(lambda cls: None))
    with pytest.raises(ImproperlyConfigured, match="no routine_policy"):
        AGUIServer(_registry(), model=_IDLE, routine_model=TeamRoutine, id="helper")


def test_a_dotted_path_for_the_model_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="routine_model"):
        AGUIServer(_registry(), model=_IDLE, routine_model="app.models.Routine", id="helper")


def test_a_create_past_the_routine_cap_is_a_400() -> None:
    ann = person("ann")
    for index in range(MAX_ROUTINES_PER_USER):
        routine(ann, f"r{index}")
    response = _call("routines", ann, "post", _DRAFT)
    assert response.status_code == 400
    assert TeamRoutine.objects.count() == MAX_ROUTINES_PER_USER


def test_the_guards_exempt_list_decides_what_the_editor_pins() -> None:
    config = build_ag_ui_config(tool_guard=ToolGuardConfig(exempt=frozenset({"drop_table"})))
    view, _ = _routes(config=config)["routines"]
    ann = person("ann")
    grantable = _json(view(request_as(ann)))["grantable"]
    assert {"name": "drop_table", "destructive": False} in grantable
    body = {**_DRAFT, "preapprovedTools": ["drop_table"]}
    assert view(request_as(ann, "post", body)).status_code == 201
