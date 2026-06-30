"""A fixture ``name -> spec`` mapping for the SpecToolset bridge tests."""

from __future__ import annotations

from typing import Any

from rest_framework_services import ServiceSpec


def ping(user: Any) -> dict[str, Any]:
    """Ping the server."""
    return {"ok": True}


SPECS: dict[str, Any] = {"ping": ServiceSpec(service=ping, atomic=False)}
