from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize, auth_error_response

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated

_RequestCancel = Callable[..., Awaitable[bool]]


class CancelView:
    """``threads/<id>/cancel/``: records a cancel for the thread's live run, for its owner."""

    def __init__(
        self,
        request_cancel: _RequestCancel,
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        self._request_cancel = request_cancel
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
            recorded = await self._request_cancel(thread_id, request=request)
        except AnonymousOperationError:
            return auth_error_response(403)
        if not recorded:
            body = {"error": "the thread has no run in progress", "thread_id": thread_id}
            return JsonResponse(body, status=404)
        return JsonResponse({"thread_id": thread_id, "cancel_requested": True}, status=202)


__all__ = ["CancelView"]
