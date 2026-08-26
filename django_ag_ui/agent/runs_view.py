from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from django_pydantic_agent.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response
from pydantic_ai.messages import UserPromptPart

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
    """Owner-scoped run index — what a client may resume or fork (async, JSON).

    Mounted by [`AGUIServer`][django_ag_ui.AGUIServer] next to ``resume/<run_id>/`` and
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

    ``preview`` is the run's first user message, whitespace-collapsed and
    truncated: the one field in the row a person can actually recognise a
    conversation by. It comes out of the snapshot this view already loads to
    answer ``continuable``, so it costs no extra query — and it is ``null``
    exactly where that snapshot is absent, which is where ``continuable`` is
    ``false`` and there is nothing to offer anyway. Without it a picker can only
    show the time and an opaque id, and two runs a minute apart are
    indistinguishable.

    **Bounded by ``RUN_LIST_LIMIT``, newest first.** A row is not cheap: each one
    loads that run's last snapshot and holds its whole message list resident
    while the response is built, so an account with a long history would
    otherwise cost one query and one full transcript *per recorded run* on a
    single GET. The newest ``run_list_limit`` runs are the ones expanded; the
    rest are dropped before any snapshot is read. There is no ``?limit`` here,
    unlike ``threads/``: the step-store protocol offers no offset, so a smaller
    page would be a client asking for less of a list it cannot page through.

    **Newest first is this view's doing.** A ``StepStore`` answers oldest-first
    — the harness protocol documents ascending ``started_at`` so a caller can
    take the newest with ``[-1]`` — and a person scanning a list wants the newest
    at the top, so the reversal happens here rather than in the store.

    Owner-scoped: the store filters by owner, so another user's runs are absent
    rather than a 403 that would confirm the id exists. Carries the same
    authentication seam as [`DjangoAGUIView`][django_ag_ui.DjangoAGUIView], closed by
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
        config: AGUIConfig | None = None,
    ) -> None:
        # A ``request -> StepStore`` factory, not a store: the harness protocol's
        # methods carry no request, so the store binds one and is built per call.
        self._step_store = step_store
        self._config: AGUIConfig = config if config is not None else build_ag_ui_config()
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
        runs = _newest(await store.list_runs(), self._config.run_list_limit)
        rows = []
        for record in runs:
            # The same call ``resume`` makes, so ``continuable`` answers exactly
            # "would resuming find a checkpoint" rather than approximating it
            # from event counts. The row's ``preview`` is read from it too, which
            # is why the snapshot is passed whole rather than reduced to a bool.
            snapshot = await store.latest_snapshot(run_id=record.run_id)
            rows.append(_run_to_json(record, snapshot=snapshot))
        # Reversed rather than sorted: the store's order is a documented ascending
        # one, so this is its exact inverse and needs no answer for a record whose
        # ``started_at`` is absent.
        rows.reverse()
        return JsonResponse({"runs": rows})


def _newest(runs: list[Any], limit: int) -> list[Any]:
    """The last ``limit`` records of a store's ascending list, still ascending.

    Applied *before* the per-run snapshot loads, which is the point: the cap
    bounds the expensive half rather than trimming rows already paid for. A
    ``limit`` of ``0`` disables it.

    The store's own ``list_runs`` takes no limit — the harness protocol has none
    to pass — so the metadata query itself stays unbounded. What this bounds is
    the N snapshot loads and the N message lists held resident behind them.
    """
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
    """The first thing the user said in this run, or ``None`` if they said nothing.

    A snapshot's messages are the run's own history, so the opening user prompt is
    what the conversation is *about* — every later turn is an answer to it. ``None``
    covers the shapes carrying no words to show: a run seeded from history alone,
    or a first prompt that is an image with no caption.
    """
    for message in snapshot.messages:
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            text = _one_line(part.content)
            if text is not None:
                return text
    return None


def _one_line(content: Any) -> str | None:
    """Collapse a user prompt to one short line, or ``None`` if it holds no text.

    A prompt is either a string or a sequence of multi-modal items, of which the
    strings are the part a person typed. Newlines and runs of spaces collapse
    because this lands in a single-line row: a pasted block would otherwise arrive
    as a paragraph for the client to clean up.
    """
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
