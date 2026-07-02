from __future__ import annotations

import json
from types import SimpleNamespace

from django.test import RequestFactory

from django_ag_ui.utils import (
    aauthorize,
    auth_error_response,
    authorize,
    call_get_user,
    materialize_request_user,
)

factory = RequestFactory()


def test_sync_authorize_open_by_default_without_user() -> None:
    request = factory.get("/agent/tools/")
    assert authorize(request, get_user=None, require_authenticated=False) is None


def test_sync_authorize_rejects_anonymous_when_gated() -> None:
    request = factory.get("/agent/tools/")
    assert authorize(request, get_user=None, require_authenticated=True) == 401


def test_sync_authorize_accepts_middleware_user() -> None:
    request = factory.get("/agent/tools/")
    request.user = SimpleNamespace(is_authenticated=True)
    assert authorize(request, get_user=None, require_authenticated=True) is None


def test_sync_authorize_predicate_forbids_with_403() -> None:
    request = factory.get("/agent/tools/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=False)
    deny = authorize(
        request,
        get_user=None,
        require_authenticated=True,
        authorize=lambda r: r.user.is_staff,
    )
    assert deny == 403


def test_sync_authorize_predicate_allows_when_it_passes() -> None:
    request = factory.get("/agent/tools/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
    deny = authorize(
        request,
        get_user=None,
        require_authenticated=True,
        authorize=lambda r: r.user.is_staff,
    )
    assert deny is None


def test_sync_authenticated_gate_precedes_predicate() -> None:
    # Anonymous + a staff predicate → 401 (the auth gate wins), not 403.
    request = factory.get("/agent/tools/")
    request.user = SimpleNamespace(is_authenticated=False, is_staff=False)
    deny = authorize(
        request,
        get_user=None,
        require_authenticated=True,
        authorize=lambda r: r.user.is_staff,
    )
    assert deny == 401


def test_call_get_user_sync_hook() -> None:
    user = SimpleNamespace(is_authenticated=True)
    assert call_get_user(lambda _request: user, factory.get("/")) is user


def test_call_get_user_async_hook_is_bridged() -> None:
    user = SimpleNamespace(is_authenticated=True)

    async def hook(request):  # noqa: ANN001, ANN202
        return user

    assert call_get_user(hook, factory.get("/")) is user


def test_call_get_user_sync_hook_returning_a_coroutine_is_consumed() -> None:
    user = SimpleNamespace(is_authenticated=True)

    async def _lookup() -> SimpleNamespace:
        return user

    assert call_get_user(lambda _request: _lookup(), factory.get("/")) is user


def test_sync_authorize_assigns_hook_user_onto_request() -> None:
    user = SimpleNamespace(is_authenticated=True)
    request = factory.get("/agent/tools/")
    assert authorize(request, get_user=lambda _r: user, require_authenticated=True) is None
    assert request.user is user


def test_materialize_request_user_handles_missing_user() -> None:
    request = factory.get("/")
    assert materialize_request_user(request) is None


async def test_async_authorize_rejects_anonymous_when_gated() -> None:
    request = factory.get("/agent/")
    request.user = SimpleNamespace(is_authenticated=False)
    assert await aauthorize(request, get_user=None, require_authenticated=True) == 401


async def test_async_authorize_predicate_forbids_with_403() -> None:
    request = factory.get("/agent/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=False)
    deny = await aauthorize(
        request,
        get_user=None,
        require_authenticated=True,
        authorize=lambda r: r.user.is_staff,
    )
    assert deny == 403


async def test_async_authorize_predicate_allows_when_it_passes() -> None:
    user = SimpleNamespace(is_authenticated=True, is_staff=True)
    request = factory.get("/agent/")
    deny = await aauthorize(
        request,
        get_user=lambda _r: user,
        require_authenticated=True,
        authorize=lambda r: r.user.is_staff,
    )
    assert deny is None


def test_auth_error_response_maps_status_to_message() -> None:
    unauth = auth_error_response(401)
    assert unauth.status_code == 401
    assert json.loads(unauth.content)["error"] == "authentication required"
    forbidden = auth_error_response(403)
    assert forbidden.status_code == 403
    assert json.loads(forbidden.content)["error"] == "forbidden"
