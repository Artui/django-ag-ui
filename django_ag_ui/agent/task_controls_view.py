from __future__ import annotations

import json
from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.types.run_store import RunStore
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.agent.live_runs import LiveRuns
from django_ag_ui.agent.live_tasks import LiveTasks
from django_ag_ui.agent.subagent_progress import TASK_TEXT_LIMIT
from django_ag_ui.agent.task_control_refused import TaskControlRefused
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated


class TaskControlsView:
    """``threads/<id>/tasks/`` and its ``steer/``, ``answer/``, ``cancel/``: the live run's tasks.

    Only a run live in this process is reached; any other answers ``404`` ``not_live_here``."""

    def __init__(
        self,
        run_store: RunStore,
        live_runs: LiveRuns | None,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        self._run_store = run_store
        self._live_runs = live_runs
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        markcoroutinefunction(cast("Any", self))

    async def __call__(
        self, request: HttpRequest, thread_id: str, task_id: str = "", *, action: str
    ) -> HttpResponseBase:
        deny = await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        method = "GET" if action == "list" else "POST"
        if request.method != method:
            return HttpResponseNotAllowed([method])
        try:
            run_id, tasks = await self._live(request, thread_id)
            if action == "list":
                listed = await tasks.tasks()
                return JsonResponse({"thread_id": thread_id, "run_id": run_id, "tasks": listed})
            return await self._control(tasks, action, task_id, _body(request), thread_id)
        except AnonymousOperationError:
            return auth_error_response(403)
        except TaskControlRefused as refused:
            body = {"error": refused.error, "reason": refused.reason, "thread_id": thread_id}
            if task_id:
                body["task_id"] = task_id
            return JsonResponse(body, status=refused.status)

    async def _live(self, request: HttpRequest, thread_id: str) -> tuple[str, LiveTasks]:
        state = await self._run_store.get(thread_id, request=request)
        run_id = None if state is None else state.active_run_id
        if run_id is None:
            raise TaskControlRefused(404, "no_run", "the thread has no run in progress")
        live = (
            None
            if self._live_runs is None
            else self._live_runs.find_tasks(request, thread_id, run_id)
        )
        if live is None:
            raise TaskControlRefused(404, "not_live_here", "the thread's run is not live here")
        return run_id, live

    async def _control(
        self, tasks: LiveTasks, action: str, task_id: str, body: dict[str, Any], thread_id: str
    ) -> JsonResponse:
        reply: dict[str, Any] = {"thread_id": thread_id, "task_id": task_id}
        if action == "steer":
            await tasks.steer(task_id, _text(body, "message"))
            reply["steered"] = True
        elif action == "answer":
            await tasks.answer(task_id, _text(body, "question_id"), _text(body, "answer"))
            reply["answered"] = True
        else:
            force = body.get("force", False)
            if not isinstance(force, bool):
                raise _invalid("force must be a boolean")
            await tasks.cancel(task_id, force=force)
            reply |= {"cancel_requested": True, "forced": force}
        return JsonResponse(reply, status=202)


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        raise _invalid("the body is not JSON") from None
    if not isinstance(body, dict):
        raise _invalid("the body is not a JSON object")
    return body


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{key} must be a non-blank string")
    if len(value) > TASK_TEXT_LIMIT:
        raise _invalid(f"{key} is longer than {TASK_TEXT_LIMIT} characters")
    return value


def _invalid(error: str) -> TaskControlRefused:
    return TaskControlRefused(400, "invalid", error)


__all__ = ["TaskControlsView"]
