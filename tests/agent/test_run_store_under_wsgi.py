from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.test import Client, override_settings
from django.urls import path
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.persistence.types.thread_run_status import ThreadRunStatus
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import FunctionModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.authed_request_factory import AuthedRequestFactory, authenticated_user


async def _speak(messages: list, info: Any) -> Any:
    yield "every word of the answer"


_store = DefaultRunStore()
_view = DjangoAGUIView(
    ToolRegistry(),
    model=FunctionModel(stream_function=_speak),
    csrf_exempt=True,
    run_store=_store,
    config=build_ag_ui_config(heartbeat_seconds=0, run_timeout_seconds=300),
)


async def _as_a_user(request: HttpRequest) -> HttpResponseBase:
    request.user = authenticated_user()  # ty: ignore[invalid-assignment]
    return await _view(request)


urlpatterns = [path("agent/", _as_a_user)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.filterwarnings(
    "ignore:django-ag-ui:RuntimeWarning", "ignore:StreamingHttpResponse must consume"
)
@override_settings(ROOT_URLCONF=__name__)
def test_a_run_store_mount_under_wsgi_answers_with_the_whole_run() -> None:
    body = {
        "threadId": "t1",
        "runId": "r1",
        "state": {},
        "messages": [{"id": "u1", "role": "user", "content": "hi"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    response = Client().post("/agent/", data=json.dumps(body), content_type="application/json")
    text = b"".join(response).decode()

    assert "every word of the answer" in text
    assert "RUN_FINISHED" in text
    state = async_to_sync(_store.get)("t1", request=AuthedRequestFactory().get("/"))
    assert state is not None and state.status is not ThreadRunStatus.RUNNING
