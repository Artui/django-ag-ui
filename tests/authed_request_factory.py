from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.test import AsyncRequestFactory, RequestFactory


def authenticated_user(pk: int = 1, username: str = "tester") -> SimpleNamespace:
    """The smallest object the auth gate and the owner-scoped stores accept."""
    return SimpleNamespace(is_authenticated=True, pk=pk, username=username)


class _AuthedMixin:
    """Stamps an authenticated user onto every request the factory builds."""

    def request(self, **request: Any) -> Any:
        built = super().request(**request)  # type: ignore[misc]
        built.user = authenticated_user()
        return built


class AuthedRequestFactory(_AuthedMixin, RequestFactory):
    """A ``RequestFactory`` (WSGI) whose requests carry a logged-in user."""


class AuthedAsyncRequestFactory(_AuthedMixin, AsyncRequestFactory):
    """An ``AsyncRequestFactory`` (ASGI) whose requests carry a logged-in user."""


__all__ = ["AuthedAsyncRequestFactory", "AuthedRequestFactory", "authenticated_user"]
