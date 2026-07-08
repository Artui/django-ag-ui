"""A spec mapping whose names collide with the drf-mcp fixture + read_attachment.

Used to prove the cross-toolset collision guard: ``add`` collides with the
drf-mcp ``test`` server's ``add`` tool (drf-mcp wins), ``read_attachment``
collides with the built-in attachment tool (spec wins over it), and
``unique_spec`` has no collision (it survives).
"""

from __future__ import annotations

from typing import Any

from rest_framework_services import ServiceSpec


def _svc(user: Any) -> dict[str, Any]:
    """A trivial service."""
    return {"ok": True}


SPECS: dict[str, Any] = {
    "add": ServiceSpec(service=_svc, atomic=False),
    "read_attachment": ServiceSpec(service=_svc, atomic=False),
    "unique_spec": ServiceSpec(service=_svc, atomic=False),
}
