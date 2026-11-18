from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models.signals import post_save
from django.http import HttpRequest
from django_spec_agents.skills import AbstractSkill, ReservedSkillNames, SkillPolicy, StoredSkills
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability

from django_ag_ui.agent.publish_invalidation import INVALIDATION_SINK
from django_ag_ui.skills.skills_changed import skills_changed

_RECEIVER_UID = "django_ag_ui.skills.announce_skill_saved"


class ReservingPolicy:
    """A consumer's policy that also reserves the mount's code-registered skill names."""

    def __init__(self, policy: SkillPolicy[Any], names: Iterable[str]) -> None:
        self.policy = policy
        self.names = frozenset(names)

    def visible(self, request: HttpRequest) -> Iterable[Any]:
        return self.policy.visible(request)

    def can_write(self, request: HttpRequest, skill: Any) -> bool:
        return self.policy.can_write(request, skill)

    def new_skill(self, request: HttpRequest) -> Any:
        return self.policy.new_skill(request)

    def reserved_names(self, request: HttpRequest) -> Collection[str]:
        if isinstance(self.policy, ReservedSkillNames):
            return self.names | frozenset(self.policy.reserved_names(request))
        return self.names


class StoredSkillsForRun(AbstractCapability[Any]):
    """``StoredSkills`` for the request a run's deps carry, so one agent serves every caller."""

    def __init__(self, policy: SkillPolicy[Any], *, authoring: bool) -> None:
        self.policy = policy
        self.authoring = authoring
        post_save.connect(announce_skill_saved, weak=False, dispatch_uid=_RECEIVER_UID)

    async def for_run(self, ctx: RunContext[Any]) -> AbstractCapability[Any]:
        request = getattr(ctx.deps, "call_context", {}).get("request")
        if not isinstance(request, HttpRequest):
            raise ImproperlyConfigured(
                "AGUIServer(skill_policy=) reads the run's request from "
                "deps.call_context['request']; a deps_factory must put it there"
            )
        stored = StoredSkills(self.policy, request, authoring=self.authoring)
        return await stored.for_run(ctx)


def announce_skill_saved(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Queue a skills-changed event on the run streaming in this context, once committed."""
    sink = INVALIDATION_SINK.get()
    if sink is None or not isinstance(instance, AbstractSkill):
        return
    event = skills_changed(instance.name)
    transaction.on_commit(lambda: sink.append(event))
