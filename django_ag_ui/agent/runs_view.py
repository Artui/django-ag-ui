from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from django_pydantic_agent.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

# The step-store protocol and its records live in ``pydantic-ai-harness``; both
# this view and the store are only reachable when a ``step_store`` is
# configured, which requires that extra. Typed ``Any`` so the module imports
# without it — the package core stays installable on a bare install.
StepStoreFactory = Any


class RunsView:
    """Owner-scoped run index — what a client may resume or fork (async, JSON).

    Mounted by :class:`~django_ag_ui.AGUIServer` next to ``resume/<run_id>/`` and
    ``fork/<run_id>/`` whenever a ``step_store`` is configured, the same way
    ``threads/`` mounts with a conversation store:

    - ``GET <prefix>runs/`` → the user's recorded runs, newest first
      (``{"runs": [...]}``).

    **Why this exists.** ``resume`` / ``fork`` both address a run *by id*, so
    without an index a client can only continue a run whose id it still holds —
    which rules out resuming after a page reload or from another device, most of
    what durable step persistence is for. This is the discovery half.

    Each row carries ``continuable``: whether the run has a saved snapshot to
    seed from. A run that never reached a provider-valid boundary has none, and
    resuming it would start from nothing — so a client should offer the action
    only for rows where this is ``true``, and the row is otherwise informational
    (a crashed run worth showing, not worth resuming).

    ``parent_run_id`` exposes fork lineage, so a client can show that a run
    branched from another rather than presenting a flat list of near-identical
    transcripts.

    Every operation is scoped to the acting user: the store filters by owner, so
    another user's runs are simply absent — never a 403 that would confirm the
    id exists. Carries the same authentication seam as
    :class:`~django_ag_ui.DjangoAGUIView`; defaults stay open for parity with the
    other catalog views, so lock it down whenever the agent endpoint is.
    """

    def __init__(
        self,
        step_store: StepStoreFactory,
        *,
        require_authenticated: bool = False,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
    ) -> None:
        # A ``request -> StepStore`` factory, not a store: the harness protocol's
        # methods carry no request, so the store binds one and is built per call
        # (the same reason ``AGUIServer`` holds a factory).
        self._step_store = step_store
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # Mark this callable instance async so Django awaits ``__call__`` (see
        # DjangoAGUIView for the rationale); the store operations are async.
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        # Establish + authorize the acting user first: this materializes
        # ``request.user`` off the event loop, so the store's owner scoping is
        # loop-safe on the calls below.
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
            # A model-backed store refusing an anonymous request (the default
            # unless ``allow_anonymous``): forbidden, not a server error.
            return auth_error_response(403)

    async def _list(self, request: HttpRequest) -> HttpResponseBase:
        store = self._step_store(request)
        runs = await store.list_runs()
        rows = []
        for record in runs:
            # ``latest_snapshot`` is the same call ``resume`` makes, so
            # ``continuable`` answers exactly "would resuming this find a
            # checkpoint" rather than approximating it from event counts.
            snapshot = await store.latest_snapshot(run_id=record.run_id)
            rows.append(_run_to_json(record, continuable=snapshot is not None))
        return JsonResponse({"runs": rows})


def _run_to_json(record: Any, *, continuable: bool) -> dict[str, Any]:
    """The wire shape for one run row — owner scoping stays server-side."""
    return {
        "run_id": record.run_id,
        "thread_id": record.conversation_id,
        "parent_run_id": record.parent_run_id,
        "started_at": record.started_at.isoformat() if record.started_at is not None else None,
        "continuable": continuable,
    }


__all__ = ["RunsView"]
