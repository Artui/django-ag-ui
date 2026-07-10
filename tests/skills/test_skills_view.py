from __future__ import annotations

import json

from django.test import RequestFactory

from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView


def _registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.add("summarize", "Summarize", "Summarize this.", chip=True)
    return reg


def test_get_returns_the_skill_catalog() -> None:
    view = SkillsView(_registry())
    response = view(RequestFactory().get("/agent/skills/"))
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload == [
        {"name": "summarize", "title": "Summarize", "prompt": "Summarize this.", "chip": True},
    ]


def test_non_get_is_rejected() -> None:
    view = SkillsView(_registry())
    response = view(RequestFactory().post("/agent/skills/"))
    assert response.status_code == 405


def test_anonymous_is_rejected_when_require_authenticated() -> None:
    view = SkillsView(_registry(), require_authenticated=True)
    response = view(RequestFactory().get("/agent/skills/"))
    assert response.status_code == 401


def test_async_get_user_hook_opens_the_catalog() -> None:
    from types import SimpleNamespace

    async def get_user(request):  # noqa: ANN001, ANN202
        return SimpleNamespace(is_authenticated=True)

    view = SkillsView(_registry(), require_authenticated=True, get_user=get_user)
    response = view(RequestFactory().get("/agent/skills/"))
    assert response.status_code == 200
