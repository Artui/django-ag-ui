from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.utils import AuthorizePredicate, auth_error_response, authorize

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.skills.skill_registry import SkillRegistry


class SkillsView:
    """A read-only endpoint returning a
    [`SkillRegistry`][django_ag_ui.SkillRegistry]'s client catalog.

    A callable instance (like [`DjangoAGUIView`][django_ag_ui.DjangoAGUIView])
    so it can hold the registry and a project can mount several. ``GET`` returns
    the JSON skill list the frontend fetches via ``data-skills-url``.

    Skill prompts can encode internal workflows — worth gating — so the view
    carries the same authentication seam as ``DjangoAGUIView``
    (``require_authenticated`` / ``get_user``, sync or async) and the same closed
    default: an anonymous ``GET`` is a ``401`` until you pass
    ``require_authenticated=False``.

    **Per caller with ``policy=``.** Given a django-spec-agents ``SkillPolicy``,
    the catalog also lists the caller's stored skills: the enabled ones
    ``list_visible_skills`` resolves, one per name, that are user-invocable.
    A stored entry carries ``name``, ``title`` (the name) and ``description``,
    and never a ``prompt``: a pick sends ``/name`` and the body loads on the
    server. A code-registered skill wins a name both hold. See
    [User skills](user-skills.md).
    """

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        *,
        policy: Any = None,
        require_authenticated: bool = True,
        get_user: Callable[[HttpRequest], Any]
        | Callable[[HttpRequest], Awaitable[Any]]
        | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        self._registry = registry if registry is not None else SkillRegistry()
        self._skill_policy = policy
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
        return JsonResponse(self.payload(request), safe=False)

    def payload(self, request: HttpRequest) -> list[dict[str, Any]]:
        """The catalog for this caller: the registry's skills, then their stored ones."""
        catalog = self._registry.payload()
        if self._skill_policy is None:
            return catalog
        # Imported late: django_spec_agents.skills defines a model, so needs the app registry.
        from django_spec_agents.skills import list_visible_skills

        taken = {entry["name"] for entry in catalog}
        for skill in list_visible_skills(self._skill_policy, request):
            if skill.user_invocable and skill.name not in taken:
                catalog.append(
                    {"name": skill.name, "title": skill.name, "description": skill.description}
                )
        return catalog


__all__ = ["SkillsView"]
