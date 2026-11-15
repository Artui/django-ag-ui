from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai import ModelRetry

from django_ag_ui import A2UI_SCHEMA_CONTEXT_DESCRIPTION
from django_ag_ui.agent.build_a2ui_toolset import (
    _normalize_protocol_version,
    _protocol_mismatch,
    build_a2ui_toolset,
)


class _Entry:
    def __init__(self, value: str) -> None:
        self.description = A2UI_SCHEMA_CONTEXT_DESCRIPTION
        self.value = value


class _RunInput:
    def __init__(self, catalog: dict[str, Any]) -> None:
        self.context = [_Entry(json.dumps(catalog))]


@pytest.mark.parametrize(
    ("declared", "label"),
    [
        ("1.0", "1.0"),
        ("1.0.3", "1.0"),
        ("v0.9+build", "v0.9"),
        (1.0, None),
        ("1.0x", None),
        (" 1.0 ", "1.0"),
        ("\u0661.\u0660", None),
    ],
    ids=["1.0", "patch", "build", "number", "suffix", "padded", "non-ascii-digits"],
)
def test_a_declared_version_is_read_as_the_middleware_reads_it(
    declared: Any, label: str | None
) -> None:
    assert _normalize_protocol_version(declared) == label


@pytest.mark.parametrize(
    ("protocol", "component", "mismatched"),
    [
        ("1.0", {"text": {"literalString": "Hi"}}, True),
        ("1.0", {"items": [[{"path": "/a"}]]}, True),
        ("1.0", {"children": {"componentId": "row", "path": "/items"}}, False),
        ("1.0", {"text": {"@path": "/a"}, "label": {"path": "/b"}}, False),
        ("v0.9", {"action": {"@call": "submit"}}, True),
        ("v0.9", {"text": {"path": "/a"}}, False),
        ("1.0", {"action": {"event": {"name": "x"}}}, False),
    ],
    ids=["literal", "nested", "repeat-template", "mixed", "call", "matching", "plain-object"],
)
def test_bindings_must_be_spelled_as_the_catalog_declares(
    protocol: str, component: dict[str, Any], mismatched: bool
) -> None:
    components = [{"id": "root", "component": "Column", **component}]
    assert bool(_protocol_mismatch(protocol, components)) is mismatched


async def test_a_call_the_toolkit_rejects_is_a_retry_naming_the_path() -> None:
    catalog = {"catalogId": "c", "components": {"Column": {"required": ["children"]}}}
    toolset = build_a2ui_toolset(_RunInput(catalog))
    assert toolset is not None
    tool = toolset.tools["render_a2ui"]
    with pytest.raises(ModelRetry, match=r"components\[0\]\.children: .*missing required prop"):
        await tool.function(None, surfaceId="s", components=[{"id": "root", "component": "Column"}])


@pytest.mark.parametrize(
    "components",
    [{"Column": 5}, {"Column": {}, "Text": "text"}, {"Column": None}],
    ids=["number", "one-bad", "null"],
)
def test_a_catalog_whose_components_are_not_all_objects_offers_no_tool(
    components: dict[str, Any],
) -> None:
    assert build_a2ui_toolset(_RunInput({"catalogId": "c", "components": components})) is None
