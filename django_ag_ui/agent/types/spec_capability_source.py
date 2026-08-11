from __future__ import annotations

from typing import Protocol, runtime_checkable

from django_ag_ui.agent.types.spec_toolset_source import SpecToolsetSource


@runtime_checkable
class SpecCapabilitySource(Protocol):
    """An already-built spec capability handed to ``AGUIServer(service_specs=...)``.

    ``djangorestframework-pydantic-ai``'s ``SpecCapability`` is the intended
    implementation — the wrapped form of a
    :class:`~django_ag_ui.agent.types.spec_toolset_source.SpecToolsetSource`,
    accepted so ``defer_loading`` composes too. It is attached exactly as given;
    the toolset it hands back is read only for the specs that feed the tool
    catalog and the tool-name dedup.

    Matched **structurally rather than imported**: drf-pydantic-ai arrives only
    with the optional ``[spec-tools]`` extra, so this package cannot name its
    types in a signature without forcing the dependency on every install.

    ``get_toolset`` is the distinguishing member — neither a mapping, a
    ``SpecSource`` nor a bare toolset carries one, so it is what the endpoint
    checks first.
    """

    def get_toolset(self) -> SpecToolsetSource: ...


__all__ = ["SpecCapabilitySource"]
