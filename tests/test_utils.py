from __future__ import annotations

from types import SimpleNamespace

from django.test import RequestFactory

from django_ag_ui.utils import authorize, call_get_user, materialize_request_user

factory = RequestFactory()


def test_sync_authorize_open_by_default_without_user() -> None:
    request = factory.get("/agent/tools/")
    assert authorize(request, get_user=None, require_authenticated=False) is True


def test_sync_authorize_rejects_anonymous_when_gated() -> None:
    request = factory.get("/agent/tools/")
    assert authorize(request, get_user=None, require_authenticated=True) is False


def test_sync_authorize_accepts_middleware_user() -> None:
    request = factory.get("/agent/tools/")
    request.user = SimpleNamespace(is_authenticated=True)
    assert authorize(request, get_user=None, require_authenticated=True) is True


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
    assert authorize(request, get_user=lambda _r: user, require_authenticated=True) is True
    assert request.user is user


def test_materialize_request_user_handles_missing_user() -> None:
    request = factory.get("/")
    assert materialize_request_user(request) is None
