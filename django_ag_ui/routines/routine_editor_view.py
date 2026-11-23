from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_service_specs import NotPermitted
from django_spec_agents import ToolGuard
from django_spec_agents.headless.types.firing_state import FiringState
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.routines import (
    AbstractRoutine,
    InvalidRoutineError,
    PreapprovedTool,
    RoutineDraft,
    RoutineSweep,
    create_routine,
    list_routines,
    parse_schedule,
    pause_routine,
    pin_preapproved_tools,
    update_routine,
)
from django_spec_agents.utils import AuthorizePredicate, GetUser, auth_error_response, authorize
from pydantic_ai.tools import ToolDefinition

_WRITES = frozenset({"POST", "PUT", "PATCH"})
_FIRED_FIELDS = ["last_run_id", "last_thread_id"]


class _Refused(Exception):
    """A write the editor refuses before the runtime sees it, with its status and body."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(body["error"])
        self.status = status
        self.body = body


class RoutineEditorView:
    """The routines editor of one agent mount, over the routine model's ``RoutinePolicy``.

    Mounted by [`AGUIServer`][django_ag_ui.AGUIServer] with ``routine_model=`` at
    ``routines/`` and below. It serves the routines whose principal is the caller and whose
    ``agent_id`` is the mount's ``id``, that ``can_write`` admits; every write goes through
    django-spec-agents' own functions, so the name, size, count, schedule and policy rules are
    the authoring tools' rules:

    - ``GET    routines/``            → ``{"routines": [...], "grantable": [...]}``;
    - ``POST   routines/``            → create from a whole routine (``201``);
    - ``PUT    routines/<id>/``       → replace it, ``enabled`` included;
    - ``PATCH  routines/<id>/``       → ``{"enabled": false}`` pauses it, ``true`` resumes it;
    - ``POST   routines/<id>/run/``   → fire it now on its sweep's queue (``202``);
    - ``GET    routines/<id>/runs/``  → ``{"runs": [...]}``, its firings, newest first.

    A write is ``{"name", "prompt", "schedule", "timezone", "preapprovedTools",
    "enabled", "confirmDestructive"}``; the agent is always the mount's. ``grantable`` lists
    ``{"name", "destructive"}`` for each server tool a routine may be granted. A grant that
    holds a destructive tool is refused with a ``409`` naming it under ``"destructive"`` unless
    ``confirmDestructive`` names it too: the editor's form of the routine's approval card.

    An ``<id>`` outside that set is a ``404``, whichever rule left it out. A broken rule is a
    ``400``, a refused write a ``403``, a firing that cannot start now a ``409``, each
    ``{"error": "..."}``.

    ``runs/`` lists ``{"runId", "threadId", "state"}`` for each firing, newest first: the one
    the routine records, with its id and its queue's ``state``, then each earlier thread among
    the caller's newest ``thread_list_limit`` in ``conversation_store`` whose ``routine_id``
    names this routine, both ``null``. CSRF and authentication are the skill editor's: an unstated
    ``csrf_exempt`` is ``False``, and a write must be ``application/json`` (else ``415``).
    """

    def __init__(
        self,
        model: type[AbstractRoutine],
        *,
        agent_id: str,
        guard: ToolGuard,
        tool_defs: Callable[[HttpRequest], list[ToolDefinition]],
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
        conversation_store: ConversationStore | None = None,
        thread_list_limit: int | None = None,
    ) -> None:
        policy = model.routine_policy()
        assert policy is not None
        self._model = model
        self._policy = policy
        self._agent_id = agent_id
        self._guard = guard
        self._tool_defs = tool_defs
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        self.csrf_exempt = csrf_exempt is True
        self._conversations = conversation_store or NullConversationStore()
        self._thread_list_limit = thread_list_limit

    def __call__(
        self, request: HttpRequest, routine_id: str | None = None, action: str | None = None
    ) -> HttpResponseBase:
        deny = authorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        if request.method in _WRITES and request.content_type != "application/json":
            return JsonResponse({"error": "a write must be application/json"}, status=415)
        try:
            if routine_id is None:
                return self._collection(request)
            return self._item(request, routine_id, action)
        except _Refused as refused:
            return JsonResponse(refused.body, status=refused.status)
        except InvalidRoutineError as error:
            return JsonResponse({"error": str(error)}, status=400)
        except (PermissionDenied, NotPermitted) as error:
            return JsonResponse({"error": str(error)}, status=403)

    def _collection(self, request: HttpRequest) -> HttpResponseBase:
        if request.method == "GET":
            routines = [_routine_entry(routine) for routine in self._writable(request)]
            grantable = [
                {"name": tool_def.name, "destructive": self._guard.requires_approval(tool_def)}
                for tool_def in self._tool_defs(request)
            ]
            return JsonResponse({"routines": routines, "grantable": grantable})
        if request.method == "POST":
            body = _body(request)
            draft = self._draft(body)
            defs = self._tool_defs(request)
            fresh = pin_preapproved_tools(self._guard, defs, draft.preapproved_tools)
            _require_confirmed(fresh, body)
            routine = create_routine(
                self._policy, request, draft, guard=self._guard, tool_defs=defs
            )
            return JsonResponse(_routine_entry(routine), status=201)
        return HttpResponseNotAllowed(["GET", "POST"])

    def _item(self, request: HttpRequest, routine_id: str, action: str | None) -> HttpResponseBase:
        routine = next((r for r in self._writable(request) if str(r.pk) == routine_id), None)
        if routine is None:
            return JsonResponse({"error": "not found"}, status=404)
        if action == "run":
            return self._run_now(request, routine)
        if action == "runs":
            return self._runs(request, routine)
        if request.method == "PUT":
            return JsonResponse(_routine_entry(self._replace(request, routine)))
        if request.method == "PATCH":
            return JsonResponse(_routine_entry(self._toggle(request, routine)))
        return HttpResponseNotAllowed(["PUT", "PATCH"])

    def _replace(self, request: HttpRequest, routine: AbstractRoutine) -> AbstractRoutine:
        body = _body(request)
        draft = self._draft(body)
        defs = _with_held(self._tool_defs(request), routine)
        fresh = pin_preapproved_tools(self._guard, defs, draft.preapproved_tools)
        held = {tool.name: tool for tool in routine.get_preapproved_tools()}
        _require_confirmed([held.get(tool.name, tool) for tool in fresh], body)
        return update_routine(
            self._policy, request, routine, draft, guard=self._guard, tool_defs=defs
        )

    def _toggle(self, request: HttpRequest, routine: AbstractRoutine) -> AbstractRoutine:
        enabled = _body(request).get("enabled")
        if not isinstance(enabled, bool):
            raise InvalidRoutineError("PATCH takes {'enabled': true} or {'enabled': false}.")
        if not enabled:
            return pause_routine(self._policy, request, routine)
        draft = RoutineDraft(
            name=routine.name,
            agent_id=routine.agent_id,
            prompt=routine.prompt,
            schedule=routine.get_schedule(),
            timezone=routine.timezone,
            preapproved_tools=tuple(tool.name for tool in routine.get_preapproved_tools()),
            enabled=True,
        )
        return update_routine(
            self._policy,
            request,
            routine,
            draft,
            guard=self._guard,
            tool_defs=_with_held(self._tool_defs(request), routine),
        )

    def _run_now(self, request: HttpRequest, routine: AbstractRoutine) -> HttpResponseBase:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        sweep = self._model.routine_sweep()
        if sweep is None:
            raise _Refused(409, {"error": "This routine model has no sweep to fire it on."})
        thread_id = uuid.uuid4().hex
        with transaction.atomic():
            locked = self._model._default_manager.select_for_update().filter(pk=routine.pk).first()
            if locked is None:
                return JsonResponse({"error": "not found"}, status=404)
            self._require_idle(sweep, locked)
            params = {"routine_id": locked.pk, "thread_id": thread_id}
            run_id = sweep.queue.enqueue(sweep.spec_name, params, actor=locked.principal)
            locked.last_run_id, locked.last_thread_id = run_id, thread_id
            locked.save(update_fields=_FIRED_FIELDS)
        return JsonResponse({"runId": run_id, "threadId": thread_id}, status=202)

    def _require_idle(self, sweep: RoutineSweep, routine: AbstractRoutine) -> None:
        """Refuse a firing while the last one runs or waits on a person, or ``can_run`` says no."""
        last = routine.last_run_id
        if (last is not None and sweep.queue.state(last) is FiringState.PENDING) or (
            routine.last_thread_id is not None
            and self._model.thread_waits(routine.last_thread_id, routine.principal)
        ):
            raise _Refused(409, {"error": "Its last firing has not finished yet."})
        if not self._policy.can_run(routine.principal, routine):
            raise _Refused(409, {"error": "Its principal may not run it now."})

    def _runs(self, request: HttpRequest, routine: AbstractRoutine) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        last = self._last_firing(routine)
        shown = {run["threadId"] for run in last}
        listed = async_to_sync(self._conversations.list)(
            request=request, limit=self._thread_list_limit
        )
        earlier = [
            {"runId": None, "threadId": meta.thread_id, "state": None}
            for meta in listed
            if meta.routine_id == str(routine.pk) and meta.thread_id not in shown
        ]
        return JsonResponse({"runs": [*last, *earlier]})

    def _last_firing(self, routine: AbstractRoutine) -> list[dict[str, Any]]:
        """The firing the routine records, with its queue state; it may have no thread yet."""
        if routine.last_run_id is None:
            return []
        sweep = self._model.routine_sweep()
        state = None if sweep is None else sweep.queue.state(routine.last_run_id)
        run = {
            "runId": routine.last_run_id,
            "threadId": routine.last_thread_id,
            "state": None if state is None else str(state),
        }
        return [run]

    def _writable(self, request: HttpRequest) -> list[AbstractRoutine]:
        """The caller's own routines on this mount that ``can_write`` admits: all it may touch."""
        return [
            routine
            for routine in list_routines(self._policy, request)
            if routine.agent_id == self._agent_id and self._policy.can_write(request, routine)
        ]

    def _draft(self, body: dict[str, Any]) -> RoutineDraft:
        fields: dict[str, Any] = {}
        for key in ("name", "prompt", "timezone"):
            if not isinstance(body.get(key), str):
                raise InvalidRoutineError(f"A routine needs {key!r} as a string.")
            fields[key] = body[key]
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise InvalidRoutineError("'enabled' must be true or false.")
        return RoutineDraft(
            agent_id=self._agent_id,
            schedule=parse_schedule(body.get("schedule")),
            preapproved_tools=_names(body, "preapprovedTools"),
            enabled=enabled,
            **fields,
        )


def _require_confirmed(
    grant: list[PreapprovedTool] | tuple[PreapprovedTool, ...], body: Any
) -> None:
    """Refuse a grant holding a destructive tool that ``confirmDestructive`` does not name."""
    confirmed = set(_names(body, "confirmDestructive"))
    missing = [tool.name for tool in grant if tool.destructive and tool.name not in confirmed]
    if missing:
        raise _Refused(
            409,
            {
                "error": f"Granting {', '.join(missing)} lets each firing run a destructive "
                "tool unasked: name it in confirmDestructive to grant it.",
                "destructive": missing,
            },
        )


def _with_held(defs: list[ToolDefinition], routine: AbstractRoutine) -> list[ToolDefinition]:
    """A held grant the editor cannot list stays grantable; ``pin_grant`` keeps its flag."""
    offered = {tool_def.name for tool_def in defs}
    held = [t.name for t in routine.get_preapproved_tools() if t.name not in offered]
    return [*defs, *(ToolDefinition(name=name) for name in held)]


def _routine_entry(routine: AbstractRoutine) -> dict[str, Any]:
    """A routine as the editor sends it, its grant with each tool's pinned destructive flag."""
    return {
        "id": str(routine.pk),
        "name": routine.name,
        "agentId": routine.agent_id,
        "prompt": routine.prompt,
        "schedule": routine.schedule,
        "timezone": routine.timezone,
        "enabled": routine.enabled,
        "pausedReason": routine.paused_reason,
        "nextFireAt": routine.next_fire_at.isoformat() if routine.next_fire_at else None,
        "preapprovedTools": [
            {"name": tool.name, "destructive": tool.destructive}
            for tool in routine.get_preapproved_tools()
        ],
        "lastRunId": routine.last_run_id,
        "lastThreadId": routine.last_thread_id,
    }


def _names(body: dict[str, Any], key: str) -> tuple[str, ...]:
    value = body.get(key, [])
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise InvalidRoutineError(f"{key!r} must be a list of tool names.")
    return tuple(value)


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError, RecursionError) as error:
        raise InvalidRoutineError("The request body must be a JSON object.") from error
    if not isinstance(body, dict):
        raise InvalidRoutineError("The request body must be a JSON object.")
    return body


__all__ = ["RoutineEditorView"]
