from __future__ import annotations


def resolve_csrf_exempt(csrf_exempt: bool | None) -> bool:
    """Resolve the tri-state ``csrf_exempt`` argument to the flag Django reads."""
    return True if csrf_exempt is None else csrf_exempt


__all__ = ["resolve_csrf_exempt"]
