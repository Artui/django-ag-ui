"""Request factories whose requests arrive already authenticated.

Every endpoint in this package refuses anonymous callers by default. Passing
``require_authenticated=False`` per test would make the suite go on exercising
the *old* default forever, so the fixtures authenticate instead and the suite
stays on the shipped one.

Tests genuinely *about* anonymity (a store refusing an anonymous operation,
per-browser session bucketing) keep the plain factory and pass
``require_authenticated=False`` — there the open configuration is the subject.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.test import AsyncRequestFactory, RequestFactory


def authenticated_user(pk: int = 1, username: str = "tester") -> SimpleNamespace:
    """The smallest object the auth gate and the owner-scoped stores accept.

    ``is_authenticated`` clears the ``require_authenticated`` gate; ``pk`` is
    what ``resolve_owner_id`` stringifies to scope a thread / attachment / run to
    its owner. A stub rather than a real ``User`` keeps the non-DB tests out of
    ``django_db``.
    """
    return SimpleNamespace(is_authenticated=True, pk=pk, username=username)


class _AuthedMixin:
    """Stamps an authenticated user onto every request the factory builds.

    Overrides ``request()`` because that is the single seam: Django's
    ``get`` / ``post`` / ``patch`` / ``delete`` helpers all funnel through
    ``generic()``, which calls it.
    """

    def request(self, **request: Any) -> Any:
        built = super().request(**request)  # type: ignore[misc]
        built.user = authenticated_user()
        return built


class AuthedRequestFactory(_AuthedMixin, RequestFactory):
    """A ``RequestFactory`` (WSGI) whose requests carry a logged-in user."""


class AuthedAsyncRequestFactory(_AuthedMixin, AsyncRequestFactory):
    """An ``AsyncRequestFactory`` (ASGI) whose requests carry a logged-in user."""


__all__ = ["AuthedAsyncRequestFactory", "AuthedRequestFactory", "authenticated_user"]
