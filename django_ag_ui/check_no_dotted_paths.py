from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured


def check_no_dotted_paths(**collaborators: object) -> None:
    """Reject a collaborator passed as a dotted path instead of an object.

    There is no ``import_string`` in this package: every collaborator is a
    constructor argument, because ``urls.py`` can hold a live object where
    ``settings.py`` could only hold its name. A string therefore cannot be
    right — but nothing was checking, and the failure it produced was as late and
    as unhelpful as a failure gets. ``attachment_store="myapp.stores.MyStore"``
    **constructs without complaint and mounts both upload endpoints**, since the
    mount test asks only whether the store is a non-null one; the string then
    fails on the first upload, as an attribute error on a ``str``, in an endpoint
    the caller believes is configured.

    So this refuses at construction, when the URL conf is imported — the same
    moment (and for the same reason) as ``check_removed_settings``, which catches
    the settings-shaped version of the same mistake. Naming every
    offender at once matters: a project converting an old settings dict has
    several, and fixing them one deploy at a time is the slowest way to find out.

    Only arguments that are always objects are passed in. ``model=`` and
    ``instructions=`` take strings by design and are not checked.

    A list is checked element-wise as well, because ``toolsets=["myapp.Toolset"]``
    is the same mistake with a longer fuse: the scalar form fails on the first
    request to that endpoint, while a string in a toolset list survives until the
    agent is built and then fails as a missing attribute on something that was
    never a toolset.
    """
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
