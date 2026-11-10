from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.utils import AuthorizePredicate, auth_error_response, authorize

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.skills.skill_registry import SkillRegistry


class SkillsView:
    """Read-only endpoint serving a [`SkillRegistry`][django_ag_ui.SkillRegistry]'s catalog."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        require_authenticated: bool = True,
        get_user: Callable[[HttpRequest], Any]
        | Callable[[HttpRequest], Awaitable[Any]]
        | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        self._registry = registry
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        # GET is a safe method the middleware never checks, so this changes nothing today.
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        # Authorize before the method check, so a mounted endpoint looks like an absent one.
        deny = authorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        return JsonResponse(self._registry.payload(), safe=False)


__all__ = ["SkillsView"]
