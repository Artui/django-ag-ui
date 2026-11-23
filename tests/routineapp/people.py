from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone
from django_spec_agents.headless.types.firing_state import FiringState

from tests.routineapp.models import TeamRoutine

WEEKDAYS_AT_8 = {"kind": "weekly", "weekdays": [0, 1, 2, 3, 4], "at": "08:00"}


class FakeQueue:
    """A ``FiringQueue`` that records each enqueue and answers ``state`` from ``states``."""

    def __init__(self, refuse: Exception | None = None) -> None:
        self.enqueued: list[tuple[str, dict[str, Any], Any]] = []
        self.states: dict[str, FiringState] = {}
        self.refuse = refuse

    def enqueue(self, spec_name: str, params: Mapping[str, Any], *, actor: Any) -> str:
        if self.refuse is not None:
            raise self.refuse
        self.enqueued.append((spec_name, dict(params), actor))
        return f"run-{len(self.enqueued)}"

    def state(self, run_id: str | None) -> FiringState | None:
        assert run_id is not None, "a guard must not ask for a firing that never happened"
        return self.states.get(run_id)


def person(username: str, team: str = "ops") -> User:
    return User.objects.create_user(username, last_name=team)


def routine(principal: User, name: str = "standup", **fields: Any) -> TeamRoutine:
    fields.setdefault("agent_id", "helper")
    fields.setdefault("team", principal.last_name)
    return TeamRoutine.objects.create(
        principal=principal,
        created_by=principal,
        name=name,
        prompt=f"Run {name}.",
        schedule=WEEKDAYS_AT_8,
        timezone="Europe/Paris",
        next_fire_at=timezone.now(),
        **fields,
    )


def request_as(user: User, method: str = "get", body: Any = None, path: str = "/r/") -> Any:
    data = {} if body is None else {"data": json.dumps(body), "content_type": "application/json"}
    request = getattr(RequestFactory(), method)(path, **data)
    request.user = user
    return request
