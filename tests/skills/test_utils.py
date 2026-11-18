from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from django_ag_ui.agent.publish_invalidation import INVALIDATION_SINK
from django_ag_ui.skills.utils import ReservingPolicy, StoredSkillsForRun, announce_skill_saved
from tests.skillapp.models import ReservingTenantSkillPolicy, TenantSkill, TenantSkillPolicy


def test_the_mount_reserves_its_registry_names_beside_the_policys_own() -> None:
    request = SimpleNamespace()
    assert ReservingPolicy(TenantSkillPolicy(), ["summarize"]).reserved_names(request) == {
        "summarize"
    }
    merged = ReservingPolicy(ReservingTenantSkillPolicy(), ["summarize"])
    assert merged.reserved_names(request) == {"summarize", "house-style"}


@pytest.mark.parametrize("context", [{}, {"request": SimpleNamespace(user=None)}])
async def test_a_run_whose_deps_carry_no_django_request_is_refused(context: dict) -> None:
    capability = StoredSkillsForRun(TenantSkillPolicy(), authoring=False)
    ctx = SimpleNamespace(deps=SimpleNamespace(call_context=context))
    with pytest.raises(ImproperlyConfigured, match="call_context"):
        await capability.for_run(ctx)  # ty: ignore[invalid-argument-type]


def test_a_save_outside_a_run_announces_nothing() -> None:
    announce_skill_saved(TenantSkill, TenantSkill(name="triage"))
    assert INVALIDATION_SINK.get() is None


def test_only_a_skill_save_is_announced() -> None:
    sink: list = []
    token = INVALIDATION_SINK.set(sink)
    try:
        announce_skill_saved(object, object())
    finally:
        INVALIDATION_SINK.reset(token)
    assert sink == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("commits", [True, False])
def test_a_save_is_announced_only_once_its_transaction_commits(commits: bool) -> None:
    sink: list = []
    token = INVALIDATION_SINK.set(sink)
    try:
        with contextlib.suppress(RuntimeError), transaction.atomic():
            announce_skill_saved(TenantSkill, TenantSkill(name="triage"))
            assert sink == []
            if not commits:
                raise RuntimeError
    finally:
        INVALIDATION_SINK.reset(token)
    assert [event.value for event in sink] == ([{"name": "triage"}] if commits else [])
