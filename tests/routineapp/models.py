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
    def thread_waits(cls, thread_id: str | None, principal: Any) -> bool:
        assert thread_id is not None, "a guard must not ask after a thread that never was"
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


class ShiftRoutine(AbstractRoutine):
    """A routine whose only manager is ``rows``: the model has no ``objects``."""

    rows = models.Manager()

    @classmethod
    def routine_policy(cls) -> ShiftRoutinePolicy:
        return ShiftRoutinePolicy()


class ShiftRoutinePolicy(RoutinePolicy[ShiftRoutine]):
    def visible(self, request: HttpRequest) -> Iterable[ShiftRoutine]:
        return ShiftRoutine.rows.all()

    def can_write(self, request: HttpRequest, routine: ShiftRoutine) -> bool:
        return True

    def can_run(self, principal: Any, routine: ShiftRoutine) -> bool:
        return True

    def new_routine(self, request: HttpRequest) -> ShiftRoutine:
        return ShiftRoutine()
