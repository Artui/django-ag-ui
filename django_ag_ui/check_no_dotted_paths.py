from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured


def check_no_dotted_paths(**collaborators: object) -> None:
    """Reject a collaborator passed as a dotted path instead of an object."""
    named: list[str] = sorted(name for name, value in collaborators.items() if _names_a_path(value))
    if not named:
        return
    details: str = "\n".join(
        f"  {name}={collaborators[name]!r} — import it and pass the object" for name in named
    )
    raise ImproperlyConfigured(
        "AGUIServer takes live collaborators, never dotted paths: this package "
        "has no import_string, so a string is a mistake that would otherwise "
        "construct, mount its endpoints, and fail per request as an attribute "
        "error on a str.\n"
        f"{details}"
    )


def _names_a_path(value: object) -> bool:
    """Whether a collaborator argument holds a name where an object belongs."""
    if isinstance(value, str):
        return True
    return isinstance(value, (list, tuple)) and any(isinstance(item, str) for item in value)


__all__ = ["check_no_dotted_paths"]
