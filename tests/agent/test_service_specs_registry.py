"""``AGUIServer(service_specs=…)`` accepts a spec registry, not just a mapping."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel
from rest_framework.permissions import AllowAny
from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec, SpecRegistry

from django_ag_ui.agent.agui_server import AGUIServer


def _list_widgets(user: Any) -> list[Any]:
    """List widgets."""
    return []


def _create_widget(user: Any) -> dict[str, Any]:
    """Create a widget."""
    return {"ok": True}


def _selector() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST, selector=_list_widgets, permission_classes=[AllowAny]
    )


def _service() -> ServiceSpec:
    return ServiceSpec(service=_create_widget, atomic=False, permission_classes=[AllowAny])


def _bare_service() -> ServiceSpec:
    """Deliberately unguarded — the shape the construction check must catch."""
    return ServiceSpec(service=_create_widget, atomic=False)


def _registry() -> SpecRegistry:
    registry = SpecRegistry()
    registry.register("list_widgets", _selector(), tags=("read", "public"))
    registry.register("create_widget", _service(), tags=("write", "admin"))
    return registry


def _server(**kwargs: Any) -> AGUIServer:
    return AGUIServer(ToolRegistry(), model=TestModel(), **kwargs)


class TestNormalisation:
    def test_a_registry_is_stored_as_a_plain_mapping(self) -> None:
        server = _server(service_specs=_registry())
        assert server._service_specs == {
            "list_widgets": server._service_specs["list_widgets"],
            "create_widget": server._service_specs["create_widget"],
        }
        assert isinstance(server._service_specs, dict)

    def test_a_registry_matches_the_equivalent_dict(self) -> None:
        registry = _registry()
        assert (
            _server(service_specs=registry)._service_specs
            == _server(service_specs=registry.specs())._service_specs
        )

    def test_names_are_names_not_records(self) -> None:
        """The whole reason normalisation happens at the entry point.

        Iterating a registry yields ``RegisteredSpec`` records; the view reserves
        tool names by iterating this value, so records here would silently break
        collision detection.
        """
        server = _server(service_specs=_registry())
        assert sorted(server._service_specs) == ["create_widget", "list_widgets"]
        assert all(isinstance(name, str) for name in server._service_specs)

    def test_none_stays_none(self) -> None:
        assert _server()._service_specs is None

    def test_an_empty_registry_is_an_empty_mapping_not_none(self) -> None:
        """Distinct from unset: an empty registry still means "spec tools on"."""
        assert _server(service_specs=SpecRegistry())._service_specs == {}

    def test_a_plain_dict_still_works(self) -> None:
        specs = {"list_widgets": _selector()}
        assert _server(service_specs=specs)._service_specs == specs

    def test_the_callers_mapping_is_copied(self) -> None:
        """Config is resolved once, so a later mutation must not leak in."""
        specs: dict[str, Any] = {"list_widgets": _selector()}
        server = _server(service_specs=specs)
        specs["sneaked_in"] = _service()

        assert "sneaked_in" not in server._service_specs

    def test_registration_order_survives(self) -> None:
        registry = SpecRegistry()
        registry.register("b_spec", _selector())
        registry.register("a_spec", _service())

        assert list(_server(service_specs=registry)._service_specs) == ["b_spec", "a_spec"]


class TestProjections:
    def test_filtered_views_give_two_endpoints_different_surfaces(self) -> None:
        registry = _registry()
        public = _server(service_specs=registry.by_tag("public"))
        admin = _server(service_specs=registry.by_tag("admin"))

        assert list(public._service_specs) == ["list_widgets"]
        assert list(admin._service_specs) == ["create_widget"]

    def test_the_two_endpoints_share_no_state(self) -> None:
        registry = _registry()
        public = _server(service_specs=registry.by_tag("public"))
        admin = _server(service_specs=registry.by_tag("admin"))

        assert public._service_specs is not admin._service_specs


class TestReachesTheViews:
    def test_the_agent_view_gets_the_normalised_mapping(self) -> None:
        server = _server(service_specs=_registry())
        assert server._view._service_specs == server._service_specs

    def test_the_tool_catalog_view_gets_it_too(self) -> None:
        """``build_tool_catalog`` calls ``.items()`` — a registry would raise."""
        server = _server(service_specs=_registry())
        patterns, _, _ = server.urls
        tools = next(p for p in patterns if p.name == "tools")

        assert tools.callback._service_specs == server._service_specs

    def test_the_catalog_renders_every_spec(self) -> None:
        from django_pydantic_agent import build_tool_catalog

        server = _server(service_specs=_registry())
        catalog = build_tool_catalog(ToolRegistry(), service_specs=server._service_specs)

        assert [entry["name"] for entry in catalog] == ["list_widgets", "create_widget"]
        assert catalog[0]["description"] == "List widgets."


class TestUnguardedSpecs:
    """The refusal happens at construction, which is the point of doing it here.

    ``SpecCapability`` already refuses an unguarded spec — but this transport
    builds its capability **per request** (it needs the request-scoped ``seen``
    set for tool-name dedup), so relying on the upstream check alone would turn
    a misconfiguration into a 500 on the first agent call instead of a failure
    to start.
    """

    def test_a_spec_with_no_permission_classes_refuses_to_construct(self) -> None:
        registry = SpecRegistry()
        registry.register(
            "list_widgets", SelectorSpec(kind=SelectorKind.LIST, selector=_list_widgets)
        )

        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(ToolRegistry(), model=TestModel(), service_specs=registry)

        message = str(excinfo.value)
        # Naming the spec is what makes this actionable against a large registry.
        assert "'list_widgets'" in message
        assert "permission_classes" in message

    def test_the_refusal_points_at_the_migration_path(self) -> None:
        """A large registry cannot be guarded in one commit, so say what to do.

        Upstream's own message offers ``require_permissions=False``; that flag is
        not reachable through ``service_specs=``, so repeating it verbatim would
        send an operator looking for a keyword this constructor does not take.
        """
        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(ToolRegistry(), model=TestModel(), service_specs={"go": _bare_service()})

        message = str(excinfo.value)
        assert "SpecCapability" in message
        assert "require_permissions=False" in message

    def test_every_offender_is_named_at_once(self) -> None:
        """Two passes over a registry is two deploys; name them all the first time."""
        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(
                ToolRegistry(),
                model=TestModel(),
                service_specs={"a": _bare_service(), "b": _bare_service()},
            )

        assert "'a', 'b'" in str(excinfo.value)

    def test_an_empty_mapping_is_not_an_offender(self) -> None:
        """``{}`` says "no spec tools", which is a configuration, not a mistake."""
        AGUIServer(ToolRegistry(), model=TestModel(), service_specs={})
