from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory
from rest_framework_services import ServiceSpec

from django_ag_ui.integrations.build_spec_toolset import build_spec_toolset


def _request(user: Any) -> HttpRequest:
    request = RequestFactory().post("/agent/")
    request.user = user  # type: ignore[attr-defined]
    return request


def _ok(user: Any) -> dict[str, Any]:
    """A no-op spec service."""
    return {"ok": True}


def test_excludes_registry_names() -> None:
    spec = ServiceSpec(service=_ok, atomic=False)
    toolset = build_spec_toolset(
        {"ping": spec, "dup": spec},
        _request(SimpleNamespace()),
        exclude_names=frozenset({"dup"}),
    )
    # The registry-owned name is dropped (registry wins the collision).
    assert set(toolset._specs) == {"ping"}


async def test_binds_the_request_user_ignoring_run_context() -> None:
    seen: dict[str, Any] = {}

    def ping(user: Any) -> dict[str, Any]:
        """Ping."""
        seen["user"] = user
        return {"ok": True}

    user = SimpleNamespace(name="alice")
    toolset = build_spec_toolset({"ping": ServiceSpec(service=ping, atomic=False)}, _request(user))
    # The run context is ignored — get_user returns the bound request.user.
    result = await toolset.call_tool("ping", {}, SimpleNamespace(deps=None), None)
    assert result == {"ok": True}
    assert seen["user"] is user
