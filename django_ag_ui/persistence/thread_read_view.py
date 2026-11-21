from __future__ import annotations

from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.persistence.types.run_store import RunStore
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated


class ThreadReadView:
    """``threads/<id>/read/``: a ``POST`` marks the caller's thread read (``204``)."""

    def __init__(
        self,
        run_store: RunStore,
        conversation_store: ConversationStore,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        self._runs = run_store
        self._conversations = conversation_store
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        warn_if_csrf_unstated(csrf_exempt, get_user)
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
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        try:
            # The store scopes by owner, so another user's thread is a 404 here.
            if not await self._conversations.exists(thread_id, request=request):
                return JsonResponse({"error": "not found"}, status=404)
            await self._runs.mark_read(thread_id, request=request)
        except AnonymousOperationError:
            return auth_error_response(403)
        return HttpResponse(status=204)


__all__ = ["ThreadReadView"]
