from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db import models
from django.http import HttpRequest
from django_spec_agents.routines import AbstractRoutine, RoutinePolicy, RoutineSweep


class TeamRoutine(AbstractRoutine):
    """A routine scoped to a team, the user's ``last_name``; a ``locked`` one is never written."""

    team = models.CharField(max_length=50)
    sweep: RoutineSweep | None = None
    waiting: frozenset[str] = frozenset()

    @classmethod
    def routine_policy(cls) -> TeamRoutinePolicy:
        return TeamRoutinePolicy()

    @classmethod
    def routine_sweep(cls) -> RoutineSweep | None:
        return cls.sweep

    @classmethod
    def thread_waits(cls, thread_id: str, principal: Any) -> bool:
        return thread_id in cls.waiting


class TeamRoutinePolicy(RoutinePolicy[TeamRoutine]):
    def visible(self, request: HttpRequest) -> Iterable[TeamRoutine]:
        return TeamRoutine.objects.filter(team=request.user.last_name).order_by("pk")

    def can_write(self, request: HttpRequest, routine: TeamRoutine) -> bool:
        return routine.team == request.user.last_name and not routine.name.startswith("locked")

    def can_run(self, principal: Any, routine: TeamRoutine) -> bool:
        return routine.name != "grounded"

    def new_routine(self, request: HttpRequest) -> TeamRoutine:
        return TeamRoutine(team=request.user.last_name)
