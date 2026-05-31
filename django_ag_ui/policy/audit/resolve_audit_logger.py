from __future__ import annotations

from importlib import import_module

from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger


def resolve_audit_logger(dotted_path: str | None) -> AuditLogger:
    """Instantiate the audit logger referenced by a dotted path.

    ``None`` yields a ``NullAuditLogger``. The path must point to a class
    importable with no arguments; consumers that need a parameterised
    logger should construct the instance themselves and pass it to the
    view directly.
    """
    if dotted_path is None:
        return NullAuditLogger()
    module_path, _, attr = dotted_path.rpartition(".")
    if not module_path:
        raise ValueError(f"invalid audit logger path: {dotted_path!r}")
    cls = getattr(import_module(module_path), attr)
    instance = cls()
    if not isinstance(instance, AuditLogger):
        raise TypeError(
            f"{dotted_path} did not produce an AuditLogger; got {type(instance).__name__}",
        )
    return instance


__all__ = ["resolve_audit_logger"]
