from __future__ import annotations

import json

import pytest
from django.test import RequestFactory

from django_ag_ui.skills.skill_registry import SkillRegistry
from django_ag_ui.skills.skills_view import SkillsView
from tests.authed_request_factory import AuthedRequestFactory
from tests.skillapp.models import Tenant, TenantSkillPolicy
from tests.skillapp.people import member, request_as, skill


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
    # The plain factory builds no .user, so the request is anonymous and the catalog refuses it.
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
    """Authorization comes before the method check, as on the agent endpoint."""
    response = SkillsView(_registry())(RequestFactory().post("/agent/skills/"))
    assert response.status_code == 401


@pytest.mark.django_db
def test_two_members_of_one_tenant_each_see_their_own_personal_skills() -> None:
    acme = Tenant.objects.create(name="acme")
    ann, bob = member("ann", acme), member("bob", acme)
    skill(acme, "ann-notes", ann)
    skill(acme, "bob-notes", bob)
    skill(acme, "house-style")
    view = SkillsView(_registry(), policy=TenantSkillPolicy())
    names = {
        user.username: [entry["name"] for entry in json.loads(view(request_as(user)).content)]
        for user in (ann, bob)
    }
    assert names == {
        "ann": ["summarize", "ann-notes", "house-style"],
        "bob": ["summarize", "bob-notes", "house-style"],
    }


@pytest.mark.django_db
def test_a_stored_entry_names_the_skill_and_never_carries_its_body() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    skill(acme, "triage", ann, description="Sort the inbox.")
    response = SkillsView(policy=TenantSkillPolicy())(request_as(ann))
    assert json.loads(response.content) == [
        {"name": "triage", "title": "triage", "description": "Sort the inbox."}
    ]
    assert b"secret steps" not in response.content


@pytest.mark.django_db
def test_the_catalog_leaves_out_what_the_user_cannot_invoke() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    skill(acme, "agent-only", ann, user_invocable=False)
    skill(acme, "switched-off", ann, enabled=False)
    response = SkillsView(policy=TenantSkillPolicy())(request_as(ann))
    assert json.loads(response.content) == []


@pytest.mark.django_db
def test_a_code_registered_skill_wins_the_name_over_a_stored_one() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    skill(acme, "summarize", ann)
    response = SkillsView(_registry(), policy=TenantSkillPolicy())(request_as(ann))
    assert [entry["name"] for entry in json.loads(response.content)] == ["summarize"]
    assert json.loads(response.content)[0]["title"] == "Summarize"
