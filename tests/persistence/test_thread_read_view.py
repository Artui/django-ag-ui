from __future__ import annotations

from typing import Any

from django.test import AsyncRequestFactory
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError

from django_ag_ui.persistence.thread_read_view import ThreadReadView


class _AnonymousStore:
    async def exists(self, thread_id: str, *, request: Any) -> bool:
        raise AnonymousOperationError("no owner")


async def test_read_answers_403_when_the_store_has_no_owner_for_the_caller() -> None:
    view = ThreadReadView(DefaultRunStore(), _AnonymousStore(), require_authenticated=False)

    response = await view(AsyncRequestFactory().post("/agent/threads/t1/read/"), thread_id="t1")

    assert response.status_code == 403
