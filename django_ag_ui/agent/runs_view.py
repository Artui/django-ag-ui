from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response
from pydantic_ai.messages import ToolReturnPart, UserPromptPart

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt

# Long enough to tell two requests apart, short enough for one line of a picker.
_PREVIEW_LIMIT = 100

# The step-store protocol and its records live in ``pydantic-ai-harness``; both
# this view and the store are only reachable when a ``step_store`` is
# configured, which requires that extra. Typed ``Any`` so the module imports
# without it — the package core stays installable on a bare install.
StepStoreFactory = Any


class RunsView:
    """Owner-scoped run index — what a client may resume or fork (async, JSON)."""

    def __init__(
        self,
        step_store: StepStoreFactory,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
        config: AGUIConfig | None = None,
    ) -> None:
        # A ``request -> StepStore`` factory, not a store: the store binds one per call.
        self._step_store = step_store
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        # First, so ``request.user`` is materialized before the store's loop-safe calls.
        deny = await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        try:
            return await self._list(request)
        except AnonymousOperationError:
            return auth_error_response(403)

    async def _list(self, request: HttpRequest) -> HttpResponseBase:
        store = self._step_store(request)
        runs = _newest(await store.list_runs(), self._config.run_list_limit)
        rows = []
        for record in runs:
            # The same call ``resume`` makes, so ``continuable`` matches what resuming finds.
            snapshot = await store.latest_snapshot(run_id=record.run_id)
            rows.append(_run_to_json(record, snapshot=snapshot))
        rows.reverse()
        return JsonResponse({"runs": rows})


def _newest(runs: list[Any], limit: int) -> list[Any]:
    """The last ``limit`` records of a store's ascending list, still ascending."""
    if not limit:
        return runs
    return runs[-limit:]


def _run_to_json(record: Any, *, snapshot: Any) -> dict[str, Any]:
    """The wire shape for one run row — owner scoping stays server-side."""
    return {
        "run_id": record.run_id,
        "thread_id": record.conversation_id,
        "parent_run_id": record.parent_run_id,
        "started_at": record.started_at.isoformat() if record.started_at is not None else None,
        "continuable": snapshot is not None,
        "preview": None if snapshot is None else _preview(snapshot),
    }


def _preview(snapshot: Any) -> str | None:
    """The prompt this run was started to answer, or ``None`` if it holds no words."""
    for message in reversed(snapshot.messages):
        for part in reversed(message.parts):
            if not isinstance(part, UserPromptPart):
                continue
            preview = _one_line(part.content)
            if preview is None and _answers_a_tool(message):
                continue
            return preview
    return None


def _answers_a_tool(message: Any) -> bool:
    """Whether a message carries a tool's return, which is where a tool's file lands."""
    return any(isinstance(part, ToolReturnPart) for part in message.parts)


def _one_line(content: Any) -> str | None:
    """Collapse a user prompt to one short line, or ``None`` if it holds no text."""
    text = (
        content
        if isinstance(content, str)
        else " ".join(item for item in content if isinstance(item, str))
    )
    collapsed = " ".join(text.split())
    if collapsed == "":
        return None
    if len(collapsed) <= _PREVIEW_LIMIT:
        return collapsed
    return f"{collapsed[:_PREVIEW_LIMIT].rstrip()}…"


__all__ = ["RunsView"]
