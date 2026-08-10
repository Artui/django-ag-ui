from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from django_ag_ui.agent.fixed_window_throttle import FixedWindowThrottle


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    cache.clear()
    yield
    cache.clear()


def _request(*, user: Any = None, ip: str = "203.0.113.9") -> Any:
    request = RequestFactory().post("/agent/")
    request.META["REMOTE_ADDR"] = ip
    if user is not None:
        request.user = user
    return request


def _authenticated(pk: int) -> SimpleNamespace:
    return SimpleNamespace(is_authenticated=True, pk=pk)


class TestTheWindow:
    def test_allows_up_to_the_limit_then_reports_retry_after(self) -> None:
        throttle = FixedWindowThrottle(max_runs=2, per_seconds=60)
        request = _request()

        assert throttle.consume(request) is None
        assert throttle.consume(request) is None

        retry_after = throttle.consume(request)
        assert retry_after is not None
        assert 1 <= retry_after <= 60

    def test_never_reports_zero(self) -> None:
        # A client reads 0 as "retry immediately", which is the one answer a
        # limiter must not give: it turns a rate limit into a busy loop.
        throttle = FixedWindowThrottle(max_runs=1, per_seconds=1)
        request = _request()
        throttle.consume(request)

        for _ in range(3):
            retry_after = throttle.consume(request)
            if retry_after is not None:
                assert retry_after >= 1


class TestBuckets:
    def test_users_are_limited_separately(self) -> None:
        throttle = FixedWindowThrottle(max_runs=1, per_seconds=60)

        assert throttle.consume(_request(user=_authenticated(1))) is None
        # A different user is not affected by the first one's quota.
        assert throttle.consume(_request(user=_authenticated(2))) is None
        assert throttle.consume(_request(user=_authenticated(1))) is not None

    def test_anonymous_callers_fall_back_to_the_remote_address(self) -> None:
        throttle = FixedWindowThrottle(max_runs=1, per_seconds=60)

        assert throttle.consume(_request(ip="203.0.113.9")) is None
        assert throttle.consume(_request(ip="198.51.100.4")) is None
        assert throttle.consume(_request(ip="203.0.113.9")) is not None

    def test_a_request_with_no_remote_address_still_buckets(self) -> None:
        throttle = FixedWindowThrottle(max_runs=1, per_seconds=60)
        request = RequestFactory().post("/agent/")
        request.META.pop("REMOTE_ADDR", None)

        assert throttle.consume(request) is None
        assert throttle.consume(request) is not None

    def test_an_anonymous_user_object_buckets_by_ip_not_by_pk(self) -> None:
        # AnonymousUser has no pk and is_authenticated is False; bucketing it as
        # a user would collapse every anonymous caller into one quota.
        from django.contrib.auth.models import AnonymousUser

        throttle = FixedWindowThrottle(max_runs=1, per_seconds=60)

        assert throttle.consume(_request(user=AnonymousUser(), ip="203.0.113.9")) is None
        assert throttle.consume(_request(user=AnonymousUser(), ip="198.51.100.4")) is None
        assert throttle.consume(_request(user=AnonymousUser(), ip="203.0.113.9")) is not None

    def test_namespaces_do_not_share_a_counter(self) -> None:
        # Two limits on one endpoint — a burst and a steady-state — must not
        # spend each other's quota.
        burst = FixedWindowThrottle(max_runs=1, per_seconds=60, namespace="burst")
        steady = FixedWindowThrottle(max_runs=1, per_seconds=60, namespace="steady")
        request = _request()

        assert burst.consume(request) is None
        assert steady.consume(request) is None

    def test_a_custom_key_chooses_the_bucket_dimension(self) -> None:
        throttle = FixedWindowThrottle(
            max_runs=1, per_seconds=60, key=lambda request: request.headers.get("x-tenant", "none")
        )
        first = RequestFactory().post("/agent/", headers={"x-tenant": "acme"})
        second = RequestFactory().post("/agent/", headers={"x-tenant": "globex"})

        assert throttle.consume(first) is None
        assert throttle.consume(second) is None
        assert throttle.consume(first) is not None


class TestConstruction:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"max_runs": 0, "per_seconds": 60}, "max_runs"),
            ({"max_runs": -1, "per_seconds": 60}, "max_runs"),
            ({"max_runs": 1, "per_seconds": 0}, "per_seconds"),
            ({"max_runs": 1, "per_seconds": -5}, "per_seconds"),
        ],
    )
    def test_refuses_a_nonsense_window(self, kwargs: dict, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            FixedWindowThrottle(**kwargs)


def test_an_eviction_between_add_and_incr_is_treated_as_a_fresh_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``incr`` raises when the key is gone — evicted, or the window expired in
    the moment since ``add`` reported it present.

    Simulated rather than waited for: the real trigger is a cache eviction
    landing between two calls microseconds apart, and the branch it reaches
    decides whether the endpoint raises a 500 at everyone or re-seeds the
    window. Patching ``incr`` is the only way to make that instant reproducible.
    """
    throttle = FixedWindowThrottle(max_runs=5, per_seconds=60)
    request = _request()
    assert throttle.consume(request) is None  # seeds the window

    def _evicted(key: str) -> int:
        raise ValueError(f"key {key!r} not found")

    monkeypatch.setattr(
        "django_ag_ui.agent.fixed_window_throttle.cache.incr", _evicted, raising=True
    )

    # Re-seeded rather than raising, and counted as the first call in the window.
    assert throttle.consume(request) is None
    monkeypatch.undo()
    # The re-seed really happened: the counter resumes from that fresh 1.
    assert throttle.consume(request) is None
