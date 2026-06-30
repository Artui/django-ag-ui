from __future__ import annotations

from typing import Any

from django.test import override_settings

from django_ag_ui import ToolRegistry, tool
from django_ag_ui.agent.build_tool_catalog import build_tool_catalog

_DRF = "tests.agent.catalog_server.server"
_SPECS = "tests.integrations.drf_specs.SPECS"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg, summary="Look up a user")
    def find_user(email: str) -> dict[str, Any]:
        """Find a user by email."""
        return {}

    @tool(reg)
    def list_open_orders() -> list[Any]:
        """List the open orders."""
        return []

    return reg


@override_settings(DJANGO_AG_UI={})
def test_registry_tools_use_summary_or_prettified_name_with_description() -> None:
    by_name = {e["name"]: e for e in build_tool_catalog(_registry())}
    # Explicit @tool(summary=) wins; description comes from the docstring.
    assert by_name["find_user"]["summary"] == "Look up a user"
    assert by_name["find_user"]["description"] == "Find a user by email."
    # No summary → prettified from the tool name.
    assert by_name["list_open_orders"]["summary"] == "List open orders"


@override_settings(DJANGO_AG_UI={"DRF_MCP_SERVER": _DRF})
def test_drf_mcp_tools_resolve_display_name_then_title_then_prettified() -> None:
    by_name = {e["name"]: e for e in build_tool_catalog(ToolRegistry())}
    # display_name / display_description win.
    assert by_name["ping"]["summary"] == "Ping the service"
    assert by_name["ping"]["description"] == "Health check."
    # No display_*: summary ← title, description ← protocol description.
    assert by_name["lookup_widget"]["summary"] == "Lookup widget"
    assert by_name["lookup_widget"]["description"] == "Find a widget."
    # Nothing: summary prettified from the name, no description key.
    assert by_name["raw_tool"]["summary"] == "Raw tool"
    assert "description" not in by_name["raw_tool"]


@override_settings(DJANGO_AG_UI={"DRF_MCP_SERVER": _DRF})
def test_registry_wins_on_name_collision_with_a_drf_mcp_tool() -> None:
    reg = ToolRegistry()

    @tool(reg, summary="Local ping")
    def ping() -> dict[str, Any]:
        """Local ping."""
        return {}

    entries = [e for e in build_tool_catalog(reg) if e["name"] == "ping"]
    assert entries == [{"name": "ping", "summary": "Local ping", "description": "Local ping."}]


@override_settings(DJANGO_AG_UI={"SERVICE_SPECS": _SPECS})
def test_service_specs_appear_in_catalog_with_prettified_summary() -> None:
    by_name = {e["name"]: e for e in build_tool_catalog(ToolRegistry())}
    # No x-summary on a spec → prettified name; description ← the service docstring.
    assert by_name["ping"]["summary"] == "Ping"
    assert by_name["ping"]["description"] == "Ping the server."


@override_settings(DJANGO_AG_UI={"SERVICE_SPECS": _SPECS})
def test_registry_wins_on_name_collision_with_a_service_spec() -> None:
    reg = ToolRegistry()

    @tool(reg, summary="Local ping")
    def ping() -> dict[str, Any]:
        """Local ping."""
        return {}

    entries = [e for e in build_tool_catalog(reg) if e["name"] == "ping"]
    assert entries == [{"name": "ping", "summary": "Local ping", "description": "Local ping."}]
