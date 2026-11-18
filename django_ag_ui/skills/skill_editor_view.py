from __future__ import annotations

import json
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.skills import (
    InvalidSkillError,
    SkillDraft,
    SkillLimitError,
    SkillPolicy,
    SkillWriteRefusedError,
    create_skill,
    disable_skill,
    enable_skill,
    update_skill,
)
from django_spec_agents.utils import AuthorizePredicate, GetUser, auth_error_response, authorize

from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt
from django_ag_ui.warn_if_csrf_unstated import warn_if_csrf_unstated

_WRITES = frozenset({"POST", "PUT", "PATCH"})
_FLAGS = (("userInvocable", "user_invocable"), ("modelInvocable", "model_invocable"))


class SkillEditorView:
    """The stored-skill editor: the one endpoint that sends a skill's body to the browser.

    Mounted by [`AGUIServer`][django_ag_ui.AGUIServer] with ``skill_editor=True``
    at ``skills/editor/`` and ``skills/editor/<id>/``, over a django-spec-agents
    ``SkillPolicy``. Every write goes through the runtime's own functions, so the
    name, size, count, reserved-name and ``can_write`` rules are the same ones the
    authoring tools apply:

    - ``GET    skills/editor/``      → ``{"skills": [...]}``, every skill the
      caller sees *and* ``can_write`` admits, disabled ones included;
    - ``POST   skills/editor/``      → create from a whole skill (``201``);
    - ``PUT    skills/editor/<id>/`` → replace name, description, body and flags;
    - ``PATCH  skills/editor/<id>/`` → ``{"enabled": false}`` turns it off,
      ``{"enabled": true}`` back on.

    A skill is ``{"id", "name", "description", "body", "userInvocable",
    "modelInvocable", "enabled"}``. A write takes the same keys less ``id`` and
    ``enabled``; the two flags default to ``true``. An ``<id>`` the caller cannot
    see or write is a ``404``, whichever it is. A broken rule is a ``400``, the
    skill cap a ``409`` and a refused write a ``403``, each ``{"error": "..."}``
    with no body in it.

    The authentication seam and closed default are
    [`ThreadsView`][django_ag_ui.ThreadsView]'s. Like the threads endpoints it is
    CSRF-exempt unless ``csrf_exempt=False``, so a write must be ``application/json``
    (else ``415``): a cross-site form or ``text/plain`` post cannot send that type
    without a CORS preflight.
    """

    def __init__(
        self,
        policy: SkillPolicy[Any],
        *,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        self._policy = policy
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        warn_if_csrf_unstated(csrf_exempt, get_user)
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)

    def __call__(self, request: HttpRequest, skill_id: str | None = None) -> HttpResponseBase:
        deny = authorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            return auth_error_response(deny)
        if request.method in _WRITES and request.content_type != "application/json":
            return JsonResponse({"error": "a write must be application/json"}, status=415)
        try:
            if skill_id is None:
                return self._collection(request)
            return self._item(request, skill_id)
        except SkillLimitError as error:
            return JsonResponse({"error": str(error)}, status=409)
        except InvalidSkillError as error:
            return JsonResponse({"error": str(error)}, status=400)
        except SkillWriteRefusedError as error:
            return JsonResponse({"error": str(error)}, status=403)

    def _collection(self, request: HttpRequest) -> HttpResponseBase:
        if request.method == "GET":
            skills = [_editor_entry(skill) for skill in self._writable(request)]
            return JsonResponse({"skills": skills})
        if request.method == "POST":
            skill = create_skill(self._policy, request, _draft(request))
            return JsonResponse(_editor_entry(skill), status=201)
        return HttpResponseNotAllowed(["GET", "POST"])

    def _item(self, request: HttpRequest, skill_id: str) -> HttpResponseBase:
        if request.method not in ("PUT", "PATCH"):
            return HttpResponseNotAllowed(["PUT", "PATCH"])
        skill = next((s for s in self._writable(request) if str(s.pk) == skill_id), None)
        if skill is None:
            return JsonResponse({"error": "not found"}, status=404)
        if request.method == "PUT":
            skill = update_skill(self._policy, request, skill, _draft(request))
        elif _enabled(request):
            skill = enable_skill(self._policy, request, skill)
        else:
            skill = disable_skill(self._policy, request, skill)
        return JsonResponse(_editor_entry(skill))

    def _writable(self, request: HttpRequest) -> list[Any]:
        """Every skill the caller sees that ``can_write`` admits: the only ones it may touch."""
        visible = self._policy.visible(request)
        return [skill for skill in visible if self._policy.can_write(request, skill)]


def _editor_entry(skill: Any) -> dict[str, Any]:
    return {
        "id": str(skill.pk),
        "name": skill.name,
        "description": skill.description,
        "body": skill.body,
        "userInvocable": skill.user_invocable,
        "modelInvocable": skill.model_invocable,
        "enabled": skill.enabled,
    }


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError, RecursionError) as error:
        raise InvalidSkillError("The request body must be a JSON object.") from error
    if not isinstance(body, dict):
        raise InvalidSkillError("The request body must be a JSON object.")
    return body


def _draft(request: HttpRequest) -> SkillDraft:
    body = _body(request)
    fields: dict[str, Any] = {}
    for key in ("name", "description", "body"):
        if not isinstance(body.get(key), str):
            raise InvalidSkillError(f"A skill needs {key!r} as a string.")
        fields[key] = body[key]
    for key, field in _FLAGS:
        value = body.get(key, True)
        if not isinstance(value, bool):
            raise InvalidSkillError(f"{key!r} must be true or false.")
        fields[field] = value
    return SkillDraft(**fields)


def _enabled(request: HttpRequest) -> bool:
    value = _body(request).get("enabled")
    if not isinstance(value, bool):
        raise InvalidSkillError("PATCH takes {'enabled': true} or {'enabled': false}.")
    return value


__all__ = ["SkillEditorView"]
