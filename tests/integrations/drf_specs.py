"""A fixture ``name -> spec`` mapping of kernel specs for the SpecToolset tests."""

from __future__ import annotations

from typing import Any

from django_service_specs import ServiceSpec, Unrestricted


def ping(user: Any) -> dict[str, Any]:
    """Ping the server."""
    return {"ok": True}


SPECS: dict[str, Any] = {
    "ping": ServiceSpec(service=ping, atomic=False, permissions=[Unrestricted()])
}
