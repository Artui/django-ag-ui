from __future__ import annotations

from collections.abc import Collection, Iterable

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.http import HttpRequest
from django_spec_agents.skills import AbstractSkill, SkillPolicy


class Tenant(models.Model):
    name = models.CharField(max_length=50)


class Member(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    is_admin = models.BooleanField(default=False)


class TenantSkill(AbstractSkill):
    """A personal skill has an ``owner``; a company one has none and only an admin writes it."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE
    )

    @classmethod
    def skill_policy(cls) -> TenantSkillPolicy:
        return TenantSkillPolicy()


class TenantSkillPolicy(SkillPolicy[TenantSkill]):
    def visible(self, request: HttpRequest) -> Iterable[TenantSkill]:
        member = Member.objects.get(user=request.user)
        mine = Q(owner=request.user) | Q(owner__isnull=True)
        return TenantSkill.objects.filter(mine, tenant=member.tenant).order_by(
            F("owner").asc(nulls_last=True), "pk"
        )

    def can_write(self, request: HttpRequest, skill: TenantSkill) -> bool:
        member = Member.objects.get(user=request.user)
        if skill.tenant_id != member.tenant_id:
            return False
        return skill.owner_id == request.user.pk or (skill.owner_id is None and member.is_admin)

    def new_skill(self, request: HttpRequest) -> TenantSkill:
        member = Member.objects.get(user=request.user)
        return TenantSkill(tenant=member.tenant, owner=request.user)


class ReservingTenantSkillPolicy(TenantSkillPolicy):
    def reserved_names(self, request: HttpRequest) -> Collection[str]:
        return {"house-style"}
