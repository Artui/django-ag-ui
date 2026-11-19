from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
    StreamingHttpResponse,
)
from django.http.response import HttpResponseBase
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.persistence.types.run_log_entry import RunLogEntry
from django_spec_agents.persistence.types.run_store import RunStore
from django_spec_agents.persistence.types.thread_run_state import ThreadRunState
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.agent.heartbeat_stream import heartbeat_stream
from django_ag_ui.agent.replay_head import replay_head
from django_ag_ui.agent.utils import is_queued_input
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt

_TERMINAL = frozenset({"RUN_FINISHED", "RUN_ERROR"})
_PAGE_LIMIT = 100
_MAX_PAGE_LIMIT = 500
_POLL_SECONDS = 0.5
_UNFINISHED = "the run ended without finishing"


class EventsView:
    """``threads/<id>/events/``: a run's log replayed then tailed as SSE, or paged as JSON.

    It reads only the run store, so any process can serve it."""

    def __init__(
        self,
        run_store: RunStore,
        conversation_store: ConversationStore,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
        config: AGUIConfig | None = None,
    ) -> None:
        self._runs = run_store
        self._conversations = conversation_store
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest, thread_id: str) -> HttpResponseBase:
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
        after = _count(request.GET.get("after", "0"), lowest=0)
        if after is None:
            return JsonResponse({"error": "after must be a whole number"}, status=400)
        try:
            state = await self._runs.get(thread_id, request=request)
            run_id = None if state is None else state.active_run_id or state.last_run_id
            if state is None or run_id is None:
                body = {"error": "the thread has no run", "thread_id": thread_id}
                return JsonResponse(body, status=404)
            if "text/event-stream" in request.headers.get("Accept", ""):
                return await self._stream(request, thread_id, run_id, after)
            return await self._page(request, state, run_id, after)
        except AnonymousOperationError:
            return auth_error_response(403)

    async def _page(
        self, request: HttpRequest, state: ThreadRunState, run_id: str, after: int
    ) -> HttpResponseBase:
        limit = _count(request.GET.get("limit", str(_PAGE_LIMIT)), lowest=1)
        if limit is None or limit > _MAX_PAGE_LIMIT:
            message = f"limit must be a whole number from 1 to {_MAX_PAGE_LIMIT}"
            return JsonResponse({"error": message}, status=400)
        read = await self._runs.read(run_id, request=request, after=after, limit=limit)
        last = read[-1].seq if read else after
        entries = _kept(read)
        live = state.active_run_id == run_id
        done = not live and len(read) < limit
        etag = f'"{run_id}:{last}:{state.status}"'
        if request.headers.get("If-None-Match") == etag:
            response: HttpResponseBase = HttpResponse(status=304)
        else:
            response = JsonResponse(
                {
                    "thread_id": state.thread_id,
                    "run_id": run_id,
                    "status": str(state.status),
                    "events": [{"seq": e.seq, "event": e.event} for e in entries],
                    "after": last,
                    "done": done,
                }
            )
        response["ETag"] = etag
        response["Cache-Control"] = "no-cache"
        return response

    async def _stream(
        self, request: HttpRequest, thread_id: str, run_id: str, after: int
    ) -> HttpResponseBase:
        # The transcript first: a run that ends between the reads is then still in its log.
        stored = await self._conversations.load(thread_id, request=request)
        log = _kept(await self._runs.read(run_id, request=request))
        messages = [] if stored is None else stored.messages
        frames = self._frames(request, thread_id, run_id, log, messages, after)
        encoded: AsyncIterator[str] = frames
        if self._config.heartbeat_seconds > 0:
            encoded = heartbeat_stream(frames, interval=self._config.heartbeat_seconds)
        response = StreamingHttpResponse(encoded, content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    async def _frames(
        self,
        request: HttpRequest,
        thread_id: str,
        run_id: str,
        log: list[RunLogEntry],
        stored: list[dict[str, Any]],
        after: int,
    ) -> AsyncIterator[str]:
        while not log:
            state = await self._runs.get(thread_id, request=request)
            log = _kept(await self._runs.read(run_id, request=request))
            if not log and not _live(state, run_id):
                yield _frame(_run_error(state))
                return
            if not log:
                await asyncio.sleep(_POLL_SECONDS)
        head, cursor = replay_head(log, stored, after=after)
        for event in head:
            yield _frame(event)
        async for entry in self._tail(request, thread_id, run_id, cursor):
            yield entry

    async def _tail(
        self, request: HttpRequest, thread_id: str, run_id: str, cursor: int
    ) -> AsyncIterator[str]:
        while True:
            # The state before the read: a run seen ended has every entry in that read.
            state = await self._runs.get(thread_id, request=request)
            entries = await self._runs.read(run_id, request=request, after=cursor)
            for entry in entries:
                yield _frame(entry.event, seq=entry.seq)
                cursor = entry.seq
                if entry.event.get("type") in _TERMINAL:
                    return
            if not entries and not _live(state, run_id):
                yield _frame(_run_error(state))
                return
            if not entries:
                await asyncio.sleep(_POLL_SECONDS)


def _kept(entries: list[RunLogEntry]) -> list[RunLogEntry]:
    return [entry for entry in entries if not is_queued_input(entry.event)]


def _live(state: ThreadRunState | None, run_id: str) -> bool:
    return state is not None and state.active_run_id == run_id


def _count(raw: str, *, lowest: int) -> int | None:
    """``raw`` as an integer of at least ``lowest``, or ``None``."""
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= lowest else None


def _run_error(state: ThreadRunState | None) -> dict[str, Any]:
    """The end a replay closes on when its run stopped with no terminal event logged."""
    reason = None if state is None else state.reason or state.status
    return {"type": "RUN_ERROR", "message": _UNFINISHED, "code": str(reason or "interrupted")}


def _frame(event: dict[str, Any], *, seq: int | None = None) -> str:
    data = json.dumps(event, separators=(",", ":"))
    return f"data: {data}\n\n" if seq is None else f"id: {seq}\ndata: {data}\n\n"


__all__ = ["EventsView"]
