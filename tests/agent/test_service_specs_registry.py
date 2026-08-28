"""``AGUIServer(service_specs=…)`` accepts a spec registry, not just a mapping."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel
from rest_framework.permissions import AllowAny
from rest_framework_pydantic_ai import SpecCapability, SpecToolset
from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec, SpecRegistry

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.types.spec_capability_source import SpecCapabilitySource
from django_ag_ui.agent.types.spec_toolset_source import SpecToolsetSource

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Every shape the constructor accepts, written the way a consumer writes it —
# through the public import, with real drf-services specs. Checked by ``ty``
# rather than executed: what is under test is the *declaration*, and running
# this proves nothing the tests above have not already proved.
CONSUMER_SNIPPET = '''"""A consumer passing each accepted ``service_specs=`` shape."""

from __future__ import annotations

from typing import Any

from django_pydantic_agent.registry.tool_registry import ToolRegistry
from rest_framework.permissions import AllowAny
from rest_framework_pydantic_ai import SpecCapability, SpecToolset
from rest_framework_services import SelectorKind, SelectorSpec, SpecRegistry

from django_ag_ui import AGUIServer


def list_widgets(user: Any) -> list[Any]:
    """List widgets."""
    return []


SPECS = {
    "list_widgets": SelectorSpec(
        kind=SelectorKind.LIST, selector=list_widgets, permission_classes=[AllowAny]
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
        """Iterating a registry yields ``RegisteredSpec`` records, and the view
        reserves tool names by iterating this value — records here would silently
        break collision detection.
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


class TestTheSourceSurvivesNormalisation:
    """Flattening is lossy, so the argument as given is kept beside the mapping.

    A ``SpecRegistry`` entry carries more than its spec: its tags, and the
    ``AgentContract`` that says what a caller with no HTTP request has to be
    told. ``specs()`` returns the specs alone, so a capability built from the
    mapping is built from a registry's *output* rather than the registry --
    silently, since the result is well-formed and merely missing declarations
    nobody asked it for.
    """

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
        from rest_framework_pydantic_ai import SpecCapability

        server = _server(service_specs=SpecCapability({"list_widgets": _selector()}))

        assert server._spec_source is None
        assert server._spec_capability is not None

    def test_the_registry_is_what_the_capability_gets_built_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of keeping it: the builder must see the entries."""
        from django_pydantic_agent.integrations import build_spec_capability as builder_module

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
        from django_pydantic_agent.integrations import build_spec_capability as builder_module

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
        from django_pydantic_agent import build_tool_catalog

        server = _server(service_specs=_registry())
        catalog = build_tool_catalog(ToolRegistry(), service_specs=server._service_specs)

        assert [entry["name"] for entry in catalog] == ["list_widgets", "create_widget"]
        assert catalog[0]["description"] == "List widgets."


class TestUnguardedSpecs:
    """The refusal happens at construction, which is the point of doing it here.

    ``SpecCapability`` already refuses an unguarded spec, but this transport
    builds its capability per request, so the upstream check alone would turn a
    misconfiguration into a 500 on the first agent call instead of a failure to
    start.
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

        Upstream's message offers ``require_permissions=False``, which is not a
        keyword this constructor takes, so repeating it verbatim would send an
        operator looking for something that does not exist.
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


class TestPreBuiltToolset:
    """``service_specs=`` accepts an already-built ``SpecToolset`` / capability.

    It is the only way to reach a ``SpecToolset`` knob (``max_page_size``, an
    ``exception_map``, a ``build_context`` override) without falling back to
    ``capabilities=``, which the tool catalog never sees — so its tool-call cards
    would render unlabelled.
    """

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
        """Re-checking would take back a decision the consumer already made.

        ``require_permissions=False`` is the documented migration path for a
        registry too large to guard in one commit, and a pre-built toolset is the
        only way to reach it from here.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            toolset = SpecToolset(
                {"list_widgets": SelectorSpec(kind=SelectorKind.LIST, selector=_list_widgets)},
                require_permissions=False,
            )

        server = self._server(toolset)  # must not raise
        assert server._spec_capability is not None

    def test_a_name_the_registry_already_owns_is_refused_at_construction(self) -> None:
        """A pre-built toolset cannot be filtered, so this has to fail loudly.

        For a mapping the endpoint drops the colliding name and the registry
        wins. That option does not exist here — the object is the consumer's —
        and left alone pydantic-ai raises ``UserError`` for the duplicate
        *mid-run*, long after the catalog looked clean.
        """
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
        # Says what to do, since neither side can be silently narrowed.
        assert "Rename" in message

    def test_a_name_the_bridged_mcp_server_owns_is_refused_too(self) -> None:
        """The other claiming source, and the one that fails latest of all.

        The mapping path already excludes both the registry and the drf-mcp
        server, so checking only the registry here made the two shapes of
        ``service_specs=`` disagree about what counts as a collision — and the
        drf-mcp half surfaced as a pydantic-ai ``UserError`` mid-run, once the
        bridge yielded its own ``add``.
        """
        from tests.integrations.drf_server import server as mcp_server

        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(
                ToolRegistry(),
                model=TestModel(),
                drf_mcp_server=mcp_server,
                service_specs=SpecToolset({"add": _service()}),
            )

        message = str(excinfo.value)
        assert "'add'" in message
        # Names *which* source claimed it, since the two are renamed in
        # different places.
        assert "drf-mcp" in message

    def test_a_name_no_source_owns_still_constructs(self) -> None:
        from tests.integrations.drf_server import server as mcp_server

        AGUIServer(
            ToolRegistry(),
            model=TestModel(),
            drf_mcp_server=mcp_server,
            service_specs=SpecToolset({"list_widgets": _selector()}),
        )

    def test_the_registry_wins_the_naming_when_both_sources_claim_it(self) -> None:
        """Precedence is the view's own: registry first, then the bridge."""
        from tests.integrations.drf_server import server as mcp_server

        registry = ToolRegistry()

        @tool(registry)
        def add() -> str:
            """Collides with both the spec tool and the drf-mcp tool."""
            return "hi"

        with pytest.raises(ImproperlyConfigured) as excinfo:
            AGUIServer(
                registry,
                model=TestModel(),
                drf_mcp_server=mcp_server,
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

        # No raise: a mapping is filtered, and the registry wins the name.
        AGUIServer(registry, model=TestModel(), service_specs={"list_widgets": _selector()})

    async def test_a_knob_only_reachable_on_a_pre_built_toolset_takes_effect(self) -> None:
        """The reason to accept one at all, asserted on what the model is shown."""
        toolset = SpecToolset({"list_widgets": _selector()}, max_page_size=25)
        server = self._server(toolset)

        tools = await server._spec_capability.get_toolset().get_tools(None)
        schema = tools["list_widgets"].tool_def.parameters_json_schema["properties"]
        assert schema["limit"]["maximum"] == 25

    def test_the_declared_protocols_match_the_real_toolset_and_capability(self) -> None:
        """**A too-narrow annotation trains consumers to suppress correct code.**

        The tests above assert the behaviour; this one and the next assert the
        *declaration* keeps up with it, so a project doing the documented thing
        does not end up with a green suite, a red type checker, and an ignore
        comment on code that was never wrong.

        The cheap half, in-process: the two protocols the union names are matched
        structurally, so a member misspelled in either would still type-check
        against itself while describing nothing that exists.
        """
        assert isinstance(SpecToolset({"list_widgets": _selector()}), SpecToolsetSource)
        assert isinstance(SpecCapability({"list_widgets": _selector()}), SpecCapabilitySource)

    def test_a_type_checker_accepts_every_shape_at_the_call_site(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The durable half — the consumer's own checker, on the consumer's code.

        ``ty`` is the checker this repo gates on, so running it over a snippet
        passing all four shapes is the only assertion that fails for the same
        reason a consumer's build does. Structural conformance alone is not
        enough: ``SpecToolset`` satisfies a ``runtime_checkable`` ``SpecSource``
        at runtime (``hasattr(x, "specs")`` is true of a property too) while
        failing assignability.
        """
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
        # Proves ty resolved this package rather than reporting a clean run over
        # a file whose imports it never found — without this the assertion below
        # passes for the wrong reason.
        assert "unresolved-import" not in output, output
        assert "service_specs" not in output, output

    def test_the_view_attaches_it_and_reserves_its_names(self) -> None:
        """The server holds it; this proves the view actually composes it.

        The names are added to ``seen`` even though nothing filters the
        capability itself, so the attachment toolset built after it cannot shadow
        one of its tools.
        """
        toolset = SpecToolset({"list_widgets": _selector()})
        server = AGUIServer(ToolRegistry(), model=TestModel(), service_specs=toolset)
        seen: set[str] = set()

        capabilities = server._view._spec_capabilities(server._service_specs, seen)

        assert capabilities == [server._spec_capability]
        assert seen == {"list_widgets"}
