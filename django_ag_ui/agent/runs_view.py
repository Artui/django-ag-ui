from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from django_pydantic_agent.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt

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

    The discovery half of resume / fork, which both address a run *by id*: an
    index is what lets a client resume after a page reload or from another
    device.

    Each row carries ``continuable`` — whether the run has a saved snapshot to
    seed from — so a client offers the action only where it is ``true``; a run
    that never reached a provider-valid boundary is informational only.
    ``parent_run_id`` exposes fork lineage.

    Owner-scoped: the store filters by owner, so another user's runs are absent
    rather than a 403 that would confirm the id exists. Carries the same
    authentication seam as :class:`~django_ag_ui.DjangoAGUIView`, closed by
    default — load-bearing here, since an anonymous caller has no runs to list.
    """

    def __init__(
        self,
        step_store: StepStoreFactory,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        # A ``request -> StepStore`` factory, not a store: the harness protocol's
        # methods carry no request, so the store binds one and is built per call.
        self._step_store = step_store
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # GET is a safe method the middleware never checks, so this changes
        # nothing today. Carried so the mount's request policy stays uniform and
        # a write verb added later inherits the answer rather than silently
        # enforcing against a client that cannot produce a token.
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        # Mark this callable instance async so Django awaits ``__call__``.
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        # First, so ``request.user`` is materialized off the event loop and the
        # store's owner scoping is loop-safe on the calls below.
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
            # The same call ``resume`` makes, so ``continuable`` answers exactly
            # "would resuming find a checkpoint" rather than approximating it
            # from event counts.
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
