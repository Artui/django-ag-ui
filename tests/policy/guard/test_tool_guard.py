from __future__ import annotations

from typing import Any

from pydantic_ai.tools import ToolDefinition

from django_ag_ui.constants import DESTRUCTIVE_METADATA_KEY
from django_ag_ui.policy.guard.tool_guard import ToolGuard
from django_ag_ui.policy.guard.types.tool_guard_config import ToolGuardConfig
from django_ag_ui.registry.decorator import tool
from django_ag_ui.registry.tool_registry import ToolRegistry


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(reg, destructive=True)
    def delete_thing(target: str) -> str:
        """Delete a thing."""
        return f"deleted {target}"

    @tool(reg)
    def read_thing(target: str) -> str:
        """Read a thing (safe)."""
        return f"read {target}"

    return reg


def _def(
    name: str, *, kind: str = "function", metadata: dict[str, Any] | None = None
) -> ToolDefinition:
    return ToolDefinition(
        name=name, parameters_json_schema={"type": "object"}, kind=kind, metadata=metadata
    )


async def _prepare(guard: ToolGuard, defs: list[ToolDefinition]) -> dict[str, str]:
    # ``prepare_tools`` ignores ``ctx``; pass ``None`` (tests aren't type-checked).
    prepared = await guard.prepare_tools(None, defs)  # type: ignore[arg-type]
    return {d.name: d.kind for d in prepared}


async def test_flips_destructive_registry_tool_to_unapproved() -> None:
    guard = ToolGuard(_registry(), config=ToolGuardConfig(enabled=True))
    kinds = await _prepare(guard, [_def("delete_thing"), _def("read_thing")])
    assert kinds["delete_thing"] == "unapproved"
    assert kinds["read_thing"] == "function"


async def test_flips_metadata_marked_tool() -> None:
    # A drf-mcp bridged mutating tool carries destructiveness in metadata, not in
    # the registry — the guard must gate it just the same.
    guard = ToolGuard(ToolRegistry(), config=ToolGuardConfig(enabled=True))
    kinds = await _prepare(
        guard,
        [
            _def("mcp_delete", metadata={DESTRUCTIVE_METADATA_KEY: True}),
            _def("mcp_list", metadata={DESTRUCTIVE_METADATA_KEY: False}),
            _def("mcp_read"),
        ],
    )
    assert kinds["mcp_delete"] == "unapproved"
    assert kinds["mcp_list"] == "function"
    assert kinds["mcp_read"] == "function"


async def test_exempt_wins_over_destructive() -> None:
    guard = ToolGuard(
        _registry(),
        config=ToolGuardConfig(enabled=True, exempt=frozenset({"delete_thing"})),
    )
    kinds = await _prepare(guard, [_def("delete_thing")])
    assert kinds["delete_thing"] == "function"


async def test_require_approval_forces_a_non_destructive_tool() -> None:
    guard = ToolGuard(
        _registry(),
        config=ToolGuardConfig(enabled=True, require_approval=frozenset({"read_thing"})),
    )
    kinds = await _prepare(guard, [_def("read_thing")])
    assert kinds["read_thing"] == "unapproved"


async def test_exempt_wins_over_require_approval() -> None:
    guard = ToolGuard(
        ToolRegistry(),
        config=ToolGuardConfig(
            enabled=True,
            exempt=frozenset({"foo"}),
            require_approval=frozenset({"foo"}),
        ),
    )
    kinds = await _prepare(guard, [_def("foo")])
    assert kinds["foo"] == "function"


async def test_external_and_output_tools_are_left_alone() -> None:
    # An external (frontend) tool is already gated client-side; an output tool is
    # not executed. Only ``function`` tools are flipped, even when destructive.
    guard = ToolGuard(
        _registry(),
        config=ToolGuardConfig(enabled=True, require_approval=frozenset({"ext", "out"})),
    )
    kinds = await _prepare(
        guard,
        [
            _def("delete_thing", kind="external"),
            _def("ext", kind="external"),
            _def("out", kind="output"),
        ],
    )
    assert kinds["delete_thing"] == "external"
    assert kinds["ext"] == "external"
    assert kinds["out"] == "output"
