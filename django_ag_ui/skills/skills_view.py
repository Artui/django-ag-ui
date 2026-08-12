from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_pydantic_agent.utils import AuthorizePredicate, auth_error_response, authorize

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.skills.skill_registry import SkillRegistry


class SkillsView:
    """A read-only endpoint returning a :class:`SkillRegistry`'s client catalog.

    A callable instance (like :class:`~django_ag_ui.agent.agui_view.DjangoAGUIView`)
    so it can hold the registry and a project can mount several. ``GET`` returns
    the JSON skill list the frontend fetches via ``data-skills-url``.

    Skill prompts can encode internal workflows — worth gating — so the view
    carries the same authentication seam as ``DjangoAGUIView``
    (``require_authenticated`` / ``get_user``, sync or async) and the same closed
    default: an anonymous ``GET`` is a ``401`` until you pass
    ``require_authenticated=False``.
    """

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
        # GET is a safe method the middleware never checks, so this changes
        # nothing today. Carried so the mount's request policy stays uniform and
        # a write verb added later inherits the answer rather than silently
        # enforcing against a client that cannot produce a token.
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        deny = authorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        return JsonResponse(self._registry.payload(), safe=False)


__all__ = ["SkillsView"]
