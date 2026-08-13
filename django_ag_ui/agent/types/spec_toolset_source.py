from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SpecToolsetSource(Protocol):
    """An already-built spec toolset handed to ``AGUIServer(service_specs=...)``.

    ``djangorestframework-pydantic-ai``'s ``SpecToolset`` is the intended
    implementation: a project needing one of its knobs (``max_page_size``, an
    ``exception_map``, a ``build_context`` override, ``require_permissions=False``
    while migrating) builds the toolset itself and passes that instead of the
    mapping. The endpoint attaches it as-is while still reading its specs for the
    tool catalog.

    Matched **structurally rather than imported**, like
    ``SpecSource``:
    drf-pydantic-ai arrives only with the optional ``[spec-tools]`` extra, so
    naming ``SpecToolset`` in a signature would force the dependency on every
    install.

    **The distinguishing member is that ``specs`` is a property, not a method.**
    A ``SpecSource`` spells the same name as ``specs()``, and that difference is
    what tells the two shapes apart at runtime.
    """

    @property
    def specs(self) -> Mapping[str, Any]: ...


__all__ = ["SpecToolsetSource"]
