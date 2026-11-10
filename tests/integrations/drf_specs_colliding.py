from __future__ import annotations

from typing import Any

from django_service_specs import ServiceSpec, Unrestricted


def _svc(user: Any) -> dict[str, Any]:
    """A trivial service."""
    return {"ok": True}


SPECS: dict[str, Any] = {
    "add": ServiceSpec(service=_svc, atomic=False, permissions=[Unrestricted()]),
    "read_attachment": ServiceSpec(service=_svc, atomic=False, permissions=[Unrestricted()]),
    "unique_spec": ServiceSpec(service=_svc, atomic=False, permissions=[Unrestricted()]),
}
