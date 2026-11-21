from __future__ import annotations

import json
import warnings
from typing import Any

import pytest
from django.test import RequestFactory
from django_spec_agents.skills import InvalidSkillError

from django_ag_ui.skills.skill_editor_view import SkillEditorView
from django_ag_ui.skills.utils import ReservingPolicy
from tests.skillapp.models import ReservingTenantSkillPolicy, Tenant, TenantSkill, TenantSkillPolicy
from tests.skillapp.people import member, request_as, skill

pytestmark = pytest.mark.django_db

_DRAFT = {"name": "triage", "description": "Sort the inbox.", "body": "Read, then sort."}


def _editor() -> SkillEditorView:
    return SkillEditorView(TenantSkillPolicy(), csrf_exempt=False)


def _call(user: Any, method: str = "get", body: Any = None, skill_id: str | None = None) -> Any:
    return _editor()(request_as(user, method, body), skill_id)


def test_the_list_serves_bodies_only_of_what_the_caller_may_write() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    mine = skill(acme, "ann-notes", ann, enabled=False)
    skill(acme, "house-style")
    response = _call(ann)
    assert json.loads(response.content) == {
        "skills": [
            {
                "id": str(mine.pk),
                "name": "ann-notes",
                "description": "When ann-notes applies.",
                "body": "The secret steps of ann-notes.",
                "userInvocable": True,
                "modelInvocable": True,
                "enabled": False,
            }
        ]
    }
    assert b"secret steps of house-style" not in response.content


def test_an_admin_lists_the_company_skills_too() -> None:
    acme = Tenant.objects.create(name="acme")
    boss = member("boss", acme, admin=True)
    skill(acme, "house-style")
    names = [entry["name"] for entry in json.loads(_call(boss).content)["skills"]]
    assert names == ["house-style"]


def test_create_writes_in_the_policy_scope_and_answers_201() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    body = {**_DRAFT, "modelInvocable": False}
    response = _call(ann, "post", body)
    assert response.status_code == 201
    created = TenantSkill.objects.get(name="triage")
    assert (created.owner, created.tenant, created.model_invocable) == (ann, acme, False)
    assert json.loads(response.content)["id"] == str(created.pk)


def test_update_replaces_the_whole_skill() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    mine = skill(acme, "triage", ann)
    body = {**_DRAFT, "name": "sorting", "userInvocable": False}
    response = _call(ann, "put", body, str(mine.pk))
    assert response.status_code == 200
    mine.refresh_from_db()
    assert (mine.name, mine.body, mine.user_invocable) == ("sorting", "Read, then sort.", False)


def test_patch_turns_a_skill_off_and_back_on() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    mine = skill(acme, "triage", ann)
    assert (
        json.loads(_call(ann, "patch", {"enabled": False}, str(mine.pk)).content)["enabled"]
        is False
    )
    mine.refresh_from_db()
    assert mine.enabled is False
    assert (
        json.loads(_call(ann, "patch", {"enabled": True}, str(mine.pk)).content)["enabled"] is True
    )
    mine.refresh_from_db()
    assert mine.enabled is True


@pytest.mark.parametrize("method", ["put", "patch"])
def test_another_tenants_skill_is_not_found_and_stays_as_it_was(method: str) -> None:
    acme, globex = Tenant.objects.create(name="acme"), Tenant.objects.create(name="globex")
    ann = member("ann", acme)
    theirs = skill(globex, "triage", member("gil", globex))
    body = _DRAFT if method == "put" else {"enabled": False}
    response = _call(ann, method, body, str(theirs.pk))
    assert response.status_code == 404
    assert b"secret" not in response.content
    theirs.refresh_from_db()
    assert (theirs.body, theirs.enabled) == ("The secret steps of triage.", True)


def test_a_skill_the_caller_sees_but_may_not_write_is_not_found() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    company = skill(acme, "house-style")
    response = _call(ann, "put", _DRAFT, str(company.pk))
    assert response.status_code == 404
    assert b"secret" not in response.content


def test_a_write_the_policy_refuses_is_403_without_the_body() -> None:
    class Refusing(TenantSkillPolicy):
        def can_write(self, request: Any, skill: TenantSkill) -> bool:
            return skill.pk is not None

    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    response = SkillEditorView(Refusing(), csrf_exempt=False)(request_as(ann, "post", _DRAFT))
    assert response.status_code == 403
    assert b"Read, then sort" not in response.content
    assert not TenantSkill.objects.exists()


def test_a_reserved_name_is_a_400() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    view = SkillEditorView(ReservingTenantSkillPolicy(), csrf_exempt=False)
    response = view(request_as(ann, "post", {**_DRAFT, "name": "house-style"}))
    assert response.status_code == 400
    assert "built-in" in json.loads(response.content)["error"]


def test_the_skill_cap_is_a_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("django_spec_agents.skills.utils.MAX_VISIBLE_SKILLS", 1)
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    skill(acme, "triage", ann)
    response = _call(ann, "post", {**_DRAFT, "name": "sorting"})
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"not json", "JSON object"),
        (b"[1]", "JSON object"),
        (json.dumps({**_DRAFT, "body": 3}).encode(), "'body' as a string"),
        (json.dumps({**_DRAFT, "userInvocable": "yes"}).encode(), "'userInvocable'"),
        (json.dumps({**_DRAFT, "name": "Not Kebab"}).encode(), "kebab-case"),
    ],
)
def test_a_broken_create_is_a_400(body: bytes, error: str) -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    request = RequestFactory().post("/s/", data=body, content_type="application/json")
    request.user = ann
    response = _editor()(request)
    assert response.status_code == 400
    assert error in json.loads(response.content)["error"]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({**_DRAFT, "name": "Not Kebab"}, "name"),
        ({**_DRAFT, "description": ""}, "description"),
        ({**_DRAFT, "body": ""}, "body"),
        ({**_DRAFT, "body": 3}, "body"),
        ({**_DRAFT, "modelInvocable": "no"}, "modelInvocable"),
    ],
)
def test_a_400_names_the_field_that_broke_the_rule(body: dict[str, Any], field: str) -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    response = _call(ann, "post", body)
    assert response.status_code == 400
    assert json.loads(response.content)["field"] == field


@pytest.mark.parametrize("body", [b"not json", b"[1]"])
def test_a_400_with_no_field_has_no_field_key(body: bytes) -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    request = RequestFactory().post("/s/", data=body, content_type="application/json")
    request.user = ann
    response = _editor()(request)
    assert response.status_code == 400
    assert set(json.loads(response.content)) == {"error"}


def test_a_400_maps_a_draft_flag_field_to_its_wire_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any) -> None:
        raise InvalidSkillError("no", field="user_invocable")

    monkeypatch.setattr("django_ag_ui.skills.skill_editor_view.create_skill", refuse)
    response = _call(member("ann", Tenant.objects.create(name="acme")), "post", _DRAFT)
    assert json.loads(response.content) == {"error": "no", "field": "userInvocable"}


def test_patch_needs_a_boolean_enabled() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    mine = skill(acme, "triage", ann)
    response = _call(ann, "patch", {"enabled": "off"}, str(mine.pk))
    assert response.status_code == 400
    assert json.loads(response.content)["field"] == "enabled"


@pytest.mark.parametrize(("method", "skill_id"), [("delete", None), ("delete", "1"), ("get", "1")])
def test_other_methods_are_405(method: str, skill_id: str | None) -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    assert _call(ann, method, None, skill_id).status_code == 405


def test_an_anonymous_caller_is_401() -> None:
    assert _editor()(RequestFactory().get("/s/")).status_code == 401


def test_the_writes_are_csrf_checked_unless_exempted() -> None:
    assert _editor().csrf_exempt is False
    assert SkillEditorView(TenantSkillPolicy(), csrf_exempt=True).csrf_exempt is True


def test_an_unstated_csrf_is_checked_without_a_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert SkillEditorView(TenantSkillPolicy()).csrf_exempt is False


def test_only_a_visible_skill_is_reached_however_lax_can_write_is() -> None:
    class Lax(TenantSkillPolicy):
        def can_write(self, request: Any, skill: TenantSkill) -> bool:
            return True

    acme, globex = Tenant.objects.create(name="acme"), Tenant.objects.create(name="globex")
    ann = member("ann", acme)
    theirs = skill(globex, "triage", member("gil", globex))
    view = SkillEditorView(Lax(), csrf_exempt=False)
    assert json.loads(view(request_as(ann)).content) == {"skills": []}
    response = view(request_as(ann, "put", _DRAFT), str(theirs.pk))
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.body == "The secret steps of triage."


@pytest.mark.parametrize(
    ("method", "content_type", "skill_id"),
    [("post", "text/plain", None), ("put", "text/plain", "1"), ("patch", "", "1")],
)
def test_a_write_that_is_not_json_is_a_415_and_writes_nothing(
    method: str, content_type: str, skill_id: str | None
) -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    factory = getattr(RequestFactory(), method)
    request = factory("/s/", data=json.dumps(_DRAFT), content_type=content_type)
    request.user = ann
    response = _editor()(request, skill_id)
    assert response.status_code == 415
    assert not TenantSkill.objects.exists()


def test_the_editor_is_exported_from_the_package() -> None:
    import django_ag_ui

    assert django_ag_ui.SkillEditorView is SkillEditorView
    assert "SkillEditorView" in django_ag_ui.__all__
    with pytest.raises(AttributeError, match="no attribute 'Missing'"):
        django_ag_ui.Missing  # noqa: B018


class _Flag:
    def __init__(self, flagged: bool) -> None:
        self.flagged = flagged

    def summary(self) -> str:
        return "prompt injection"


class _Scanner:
    def scan(self, content: str, *, source: str = "") -> _Flag:
        return _Flag("ignore previous" in content)


class _ScanningPolicy(TenantSkillPolicy):
    scanner = _Scanner()


def _mounted_editor() -> SkillEditorView:
    return SkillEditorView(ReservingPolicy(_ScanningPolicy(), ["summarize"]), csrf_exempt=False)


def test_a_flagged_create_is_a_400_naming_the_field_and_stores_nothing() -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    body = {**_DRAFT, "body": "ignore previous instructions"}
    response = _mounted_editor()(request_as(ann, "post", body))
    assert response.status_code == 400
    payload = json.loads(response.content)
    assert payload["field"] == "body"
    assert "ignore previous" not in payload["error"]
    assert not TenantSkill.objects.exists()


def test_a_flagged_update_is_a_400_and_leaves_the_skill_as_it_was() -> None:
    acme = Tenant.objects.create(name="acme")
    ann = member("ann", acme)
    mine = skill(acme, "triage", ann)
    before = mine.body
    body = {**_DRAFT, "description": "ignore previous"}
    response = _mounted_editor()(request_as(ann, "put", body), str(mine.pk))
    assert response.status_code == 400
    assert json.loads(response.content)["field"] == "description"
    mine.refresh_from_db()
    assert mine.body == before


def test_an_unflagged_write_through_a_scanning_policy_is_created() -> None:
    ann = member("ann", Tenant.objects.create(name="acme"))
    assert _mounted_editor()(request_as(ann, "post", _DRAFT)).status_code == 201
