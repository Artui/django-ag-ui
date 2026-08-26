from __future__ import annotations

import json

from django.test import RequestFactory

from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView
from tests.authed_request_factory import AuthedRequestFactory


def _registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.add("summarize", "Summarize", "Summarize this.", chip=True)
    return reg


def test_get_returns_the_skill_catalog() -> None:
    view = SkillsView(_registry())
    response = view(AuthedRequestFactory().get("/agent/skills/"))
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload == [
        {"name": "summarize", "title": "Summarize", "prompt": "Summarize this.", "chip": True},
    ]


def test_non_get_is_rejected() -> None:
    view = SkillsView(_registry())
    response = view(AuthedRequestFactory().post("/agent/skills/"))
    assert response.status_code == 405


def test_anonymous_is_rejected_by_default() -> None:
    # The plain factory builds no `.user`, so the request is anonymous — and
    # the catalog refuses it without anything being passed.
    response = SkillsView(_registry())(RequestFactory().get("/agent/skills/"))
    assert response.status_code == 401


def test_anonymous_is_served_when_authentication_is_waived() -> None:
    view = SkillsView(_registry(), require_authenticated=False)
    response = view(RequestFactory().get("/agent/skills/"))
    assert response.status_code == 200


def test_async_get_user_hook_opens_the_catalog() -> None:
    from types import SimpleNamespace

    async def get_user(request):  # noqa: ANN001, ANN202
        return SimpleNamespace(is_authenticated=True)

    view = SkillsView(_registry(), require_authenticated=True, get_user=get_user)
    response = view(AuthedRequestFactory().get("/agent/skills/"))
    assert response.status_code == 200


def test_an_unauthenticated_non_get_is_401_not_405() -> None:
    """Authorization comes before the method check, as on the agent endpoint.

    A 405 here against a 404 for an unmounted backend told an unauthenticated
    caller which optional endpoints a deployment had enabled.
    """
    response = SkillsView(_registry())(RequestFactory().post("/agent/skills/"))
    assert response.status_code == 401
