from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

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


async def test_a_run_whose_deps_carry_no_request_is_refused() -> None:
    capability = StoredSkillsForRun(TenantSkillPolicy(), authoring=False)
    ctx = SimpleNamespace(deps=SimpleNamespace(call_context={}))
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
