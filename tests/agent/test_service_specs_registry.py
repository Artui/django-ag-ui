from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_agents.spec_tools import SpecCapability, SpecToolset
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.types.spec_capability_source import SpecCapabilitySource
from django_ag_ui.agent.types.spec_toolset_source import SpecToolsetSource

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Every shape the constructor accepts, written the way a consumer writes it —
# through the public import, with real django-service-specs specs. Checked by ``ty``
# rather than executed: what is under test is the *declaration*, and running
# this proves nothing the tests above have not already proved.
CONSUMER_SNIPPET = '''"""A consumer passing each accepted ``service_specs=`` shape."""

from __future__ import annotations

from typing import Any

from django_service_specs import SelectorKind, SelectorSpec, SpecRegistry, Unrestricted
from django_spec_agents.registry.tool_registry import ToolRegistry
from django_spec_agents.spec_tools import SpecCapability, SpecToolset

from django_ag_ui import AGUIServer


def list_widgets(user: Any) -> list[Any]:
    """List widgets."""
    return []


SPECS = {
    "list_widgets": SelectorSpec(
        kind=SelectorKind.LIST, selector=list_widgets, permissions=[Unrestricted()]
    )
}

AGUIServer(ToolRegistry(), service_specs=SPECS)
AGUIServer(ToolRegistry(), service_specs=SpecRegistry())
AGUIServer(ToolRegistry(), service_specs=SpecToolset(SPECS))
AGUIServer(ToolRegistry(), service_specs=SpecCapability(SPECS))
AGUIServer(ToolRegistry(), service_specs=None)
'''


def _list_widgets(user: Any) -> list[Any]:
    """List widgets."""
    return []


def _create_widget(user: Any) -> dict[str, Any]:
    """Create a widget."""
    return {"ok": True}


def _selector() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST, selector=_list_widgets, permissions=[Unrestricted()]
    )


def _service() -> ServiceSpec:
    return ServiceSpec(service=_create_widget, atomic=False, permissions=[Unrestricted()])


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
        """Records here would silently break collision detection."""
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


class TestTheSourceSurvivesNormalisation:
    """The argument as given is kept beside the mapping and handed on as given."""

    def test_a_registry_is_kept_as_the_source(self) -> None:
        registry = _registry()
        server = _server(service_specs=registry)

        assert server._spec_source is registry
        # And the entry, not just the spec, is what that preserves.
        assert server._spec_source.get("list_widgets").tags == frozenset({"read", "public"})

    def test_a_plain_mapping_has_no_source_to_keep(self) -> None:
        assert _server(service_specs={"list_widgets": _selector()})._spec_source is None

    def test_unset_has_no_source(self) -> None:
        assert _server()._spec_source is None

    def test_a_prebuilt_capability_is_not_a_source(self) -> None:
        """It is attached as itself, so nothing is built from a source for it."""
        from django_spec_agents.spec_tools import SpecCapability

        server = _server(service_specs=SpecCapability({"list_widgets": _selector()}))

        assert server._spec_source is None
        assert server._spec_capability is not None

    def test_the_registry_is_what_the_capability_gets_built_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of keeping it: the builder must see the entries."""
        from django_spec_agents.integrations import build_spec_capability as builder_module

        captured: list[Any] = []

        real = builder_module.build_spec_capability

        def _stub(specs: Any, *, exclude_names: frozenset[str] = frozenset()) -> Any:
            captured.append(specs)
            return real(specs, exclude_names=exclude_names)

        monkeypatch.setattr(builder_module, "build_spec_capability", _stub)
        registry = _registry()
        view = _server(service_specs=registry)._view

        view._spec_capabilities(view._service_specs, set())

        assert captured == [registry]

    def test_a_mapping_still_reaches_the_builder_as_a_mapping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from django_spec_agents.integrations import build_spec_capability as builder_module

        captured: list[Any] = []

        real = builder_module.build_spec_capability

        def _stub(specs: Any, *, exclude_names: frozenset[str] = frozenset()) -> Any:
            captured.append(specs)
            return real(specs, exclude_names=exclude_names)

        monkeypatch.setattr(builder_module, "build_spec_capability", _stub)
        specs = {"list_widgets": _selector()}
        view = _server(service_specs=specs)._view

        view._spec_capabilities(view._service_specs, set())

        assert captured == [specs]


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
        from django_spec_agents import build_tool_catalog

        server = _server(service_specs=_registry())
        catalog = build_tool_catalog(ToolRegistry(), service_specs=server._service_specs)

        assert [entry["name"] for entry in catalog] == ["list_widgets", "create_widget"]
        assert catalog[0]["description"] == "List widgets."


class TestUnguardedSpecs:
    """A spec with no permissions never gets as far as serving."""

    def test_a_registry_spec_with_no_permissions_is_refused_at_register(self) -> None:
        """The kernel answers first for a registry, naming the spec."""
        registry = SpecRegistry()
        with pytest.raises(ImproperlyConfigured) as excinfo:
            registry.register(
                "list_widgets", SelectorSpec(kind=SelectorKind.LIST, selector=_list_widgets)
            )

        message = str(excinfo.value)
        assert "'list_widgets'" in message
        assert "declares no permissions" in message

    def test_a_mapping_spec_with_no_permissions_is_refused_at_construction(self) -> None:
        """This transport's own check, in its own words."""
        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(
                ToolRegistry(), model=TestModel(), service_specs={"list_widgets": _bare_service()}
            )

        message = str(excinfo.value)
        assert "'list_widgets'" in message
        assert "AGUIServer(service_specs=...)" in message
        assert "permissions=[Unrestricted()]" in message

    async def test_an_explicitly_unrestricted_spec_is_served(self) -> None:
        """``permissions=[Unrestricted()]`` is a declaration, not an absence."""
        server = _server(service_specs={"create_widget": _service()})
        view = server._view

        (capability,) = view._spec_capabilities(view._service_specs, set())
        tools = await capability.get_toolset().get_tools(None)

        assert set(tools) == {"create_widget"}

    def test_specs_without_the_extra_are_refused_naming_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The core install has no kernel; say which extra brings it."""
        monkeypatch.setitem(sys.modules, "django_service_specs", None)

        with pytest.raises(ImportError) as excinfo:
            _server(service_specs={"list_widgets": object()})

        assert "django-ag-ui[specs]" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, ImportError)

    def test_a_value_that_compiles_to_no_spec_is_refused_naming_the_tool(self) -> None:
        """A value the kernel cannot read is a mistake in one entry; say which."""
        with pytest.raises(TypeError) as excinfo:
            _server(service_specs={"list_widgets": _selector(), "oops": object()})

        message = str(excinfo.value)
        assert message.startswith("AGUIServer(service_specs=...) tool 'oops': ")
        assert isinstance(excinfo.value.__cause__, TypeError)

    def test_the_refusal_points_at_the_migration_path(self) -> None:
        """A large registry cannot be guarded in one commit, so say what to do."""
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


class TestPreBuiltToolset:
    """``service_specs=`` accepts an already-built ``SpecToolset`` / capability."""

    def _server(self, source: Any) -> AGUIServer:
        return AGUIServer(ToolRegistry(), model=TestModel(), service_specs=source)

    def test_a_pre_built_toolset_is_attached_as_itself(self) -> None:
        toolset = SpecToolset({"list_widgets": _selector()}, id="mine")
        server = self._server(toolset)

        assert server._spec_capability.get_toolset() is toolset

    def test_a_pre_built_capability_is_attached_as_itself(self) -> None:
        capability = SpecCapability({"list_widgets": _selector()})
        server = self._server(capability)

        assert server._spec_capability is capability

    def test_its_specs_still_reach_the_catalog(self) -> None:
        """Choosing the powerful form must not cost the tool-call card labels."""
        server = self._server(SpecToolset({"list_widgets": _selector()}))

        assert set(server._service_specs) == {"list_widgets"}

    def test_a_toolset_built_to_skip_the_permission_check_is_not_re_checked(self) -> None:
        """Re-checking would take back a decision the consumer already made."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            toolset = SpecToolset(
                {"list_widgets": SelectorSpec(kind=SelectorKind.LIST, selector=_list_widgets)},
                require_permissions=False,
            )

        server = self._server(toolset)
        assert server._spec_capability is not None

    def test_a_name_the_registry_already_owns_is_refused_at_construction(self) -> None:
        """A pre-built toolset cannot be filtered, so this has to fail loudly."""
        registry = ToolRegistry()

        @tool(registry)
        def list_widgets() -> str:
            """Collides with the spec tool."""
            return "hi"

        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(
                registry,
                model=TestModel(),
                service_specs=SpecToolset({"list_widgets": _selector()}),
            )

        message = str(excinfo.value)
        assert "'list_widgets'" in message
        assert "Rename" in message

    def test_a_name_the_bridged_mcp_server_owns_is_refused_too(self) -> None:
        """The other claiming source, and the one that fails latest of all."""
        from tests.integrations.spec_mcp_server import server as mcp_server

        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(
                ToolRegistry(),
                model=TestModel(),
                mcp_server=mcp_server,
                service_specs=SpecToolset({"add": _service()}),
            )

        message = str(excinfo.value)
        assert "'add'" in message
        assert "the bridged MCP server" in message

    def test_a_name_no_source_owns_still_constructs(self) -> None:
        from tests.integrations.spec_mcp_server import server as mcp_server

        AGUIServer(
            ToolRegistry(),
            model=TestModel(),
            mcp_server=mcp_server,
            service_specs=SpecToolset({"list_widgets": _selector()}),
        )

    def test_the_registry_wins_the_naming_when_both_sources_claim_it(self) -> None:
        """Precedence is the view's own: registry first, then the bridge."""
        from tests.integrations.spec_mcp_server import server as mcp_server

        registry = ToolRegistry()

        @tool(registry)
        def add() -> str:
            """Collides with both the spec tool and the bridged tool."""
            return "hi"

        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(
                registry,
                model=TestModel(),
                mcp_server=mcp_server,
                service_specs=SpecToolset({"add": _service()}),
            )

        assert "@tool registry" in str(excinfo.value)

    def test_a_mapping_still_gets_the_old_precedence(self) -> None:
        """The filtering path is unchanged — only the pre-built form is strict."""
        registry = ToolRegistry()

        @tool(registry)
        def list_widgets() -> str:
            """Collides with the spec tool."""
            return "hi"

        AGUIServer(registry, model=TestModel(), service_specs={"list_widgets": _selector()})

    async def test_a_knob_only_reachable_on_a_pre_built_toolset_takes_effect(self) -> None:
        """The reason to accept one at all, asserted on what the model is shown."""
        toolset = SpecToolset({"list_widgets": _selector()}, max_page_size=25)
        server = self._server(toolset)

        tools = await server._spec_capability.get_toolset().get_tools(None)
        schema = tools["list_widgets"].tool_def.parameters_json_schema["properties"]
        assert schema["limit"]["maximum"] == 25

    def test_the_declared_protocols_match_the_real_toolset_and_capability(self) -> None:
        """**A too-narrow annotation trains consumers to suppress correct code.**"""
        assert isinstance(SpecToolset({"list_widgets": _selector()}), SpecToolsetSource)
        assert isinstance(SpecCapability({"list_widgets": _selector()}), SpecCapabilitySource)

    def test_a_type_checker_accepts_every_shape_at_the_call_site(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The durable half — the consumer's own checker, on the consumer's code."""
        if shutil.which("ty") is None:
            pytest.skip("ty is a dev-group dependency; nothing to check without it")
        snippet = tmp_path / "consumer.py"
        snippet.write_text(CONSUMER_SNIPPET)

        result = subprocess.run(
            ["ty", "check", str(snippet)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        output = result.stdout + result.stderr
        assert "unresolved-import" not in output, output
        assert "service_specs" not in output, output

    def test_the_view_attaches_it_and_reserves_its_names(self) -> None:
        """The server holds it; this proves the view actually composes it."""
        toolset = SpecToolset({"list_widgets": _selector()})
        server = AGUIServer(ToolRegistry(), model=TestModel(), service_specs=toolset)
        seen: set[str] = set()

        capabilities = server._view._spec_capabilities(server._service_specs, seen)

        assert capabilities == [server._spec_capability]
        assert seen == {"list_widgets"}
