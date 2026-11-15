from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from django.test import override_settings
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.agent.agui_view import DjangoAGUIView
from django_ag_ui.agent.build_untrusted_context import build_untrusted_context
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from tests.agent.test_agui_view import _drain, _post, _registry

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "a2ui_middleware"
GENERATED: dict[str, Any] = json.loads((FIXTURES / "final_result_snapshot.json").read_text())
CATALOG: dict[str, Any] = json.loads((FIXTURES / "basic_catalog_1_0.json").read_text())
DESCRIPTION: str = GENERATED["context_description"]
ENABLED = override_settings(DJANGO_AG_UI={"A2UI": {"ENABLED": True}})

SURFACE_ID = "validation-surface"
COMPONENTS: list[dict[str, Any]] = GENERATED["operations"][1]["updateComponents"]["components"]
DATA: dict[str, Any] = GENERATED["operations"][2]["updateDataModel"]["value"]
PLAIN_ROOT: list[dict[str, Any]] = [
    {"id": "root", "component": "Column", "children": ["t"]},
    {"id": "t", "component": "Text", "text": "Hello"},
]


def _entry(catalog: dict[str, Any]) -> dict[str, str]:
    """The context entry the renderer posts: its catalog, compact like ``JSON.stringify``."""
    return {"description": DESCRIPTION, "value": json.dumps(catalog, separators=(",", ":"))}


def _body(context: list[dict[str, str]], *, run_id: str = "r1") -> bytes:
    return json.dumps(
        {
            "threadId": "t1",
            "runId": run_id,
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "show it"}],
            "tools": [],
            "context": context,
            "forwardedProps": {},
        }
    ).encode()


def _frames(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


def _surfaces(body: str) -> list[dict[str, Any]]:
    return [
        frame
        for frame in _frames(body)
        if frame["type"] == "ACTIVITY_SNAPSHOT" and frame["activityType"] == "a2ui-surface"
    ]


def _calling(
    calls: list[tuple[str, dict[str, Any]]],
    seen: list[list[ModelMessage]],
    offered: list[list[str]],
) -> FunctionModel:
    """A model making ``calls`` in order, one per round, then answering."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> Any:
        seen.append(list(messages))
        offered.append([tool.name for tool in info.function_tools])
        rounds = sum(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart | RetryPromptPart) for part in message.parts)
            for message in messages
        )
        if rounds < len(calls) and "render_a2ui" in offered[-1]:
            call_id, args = calls[rounds]
            yield {
                0: DeltaToolCall(
                    name="render_a2ui", json_args=json.dumps(args), tool_call_id=call_id
                )
            }
            return
        yield "done"

    return FunctionModel(stream_function=stream)


async def _run(
    calls: list[tuple[str, dict[str, Any]]], context: list[dict[str, str]]
) -> tuple[str, list[list[ModelMessage]], list[list[str]]]:
    seen: list[list[ModelMessage]] = []
    offered: list[list[str]] = []
    view = DjangoAGUIView(_registry(), model=_calling(calls, seen, offered))
    body = await _drain(await view(_post(_body(context))))
    return body, seen, offered


def _retry_texts(messages: list[ModelMessage]) -> list[str]:
    """What the model was told to retry; from pydantic-ai 2.60 a retried return carries it."""
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
        or (isinstance(part, ToolReturnPart) and part.outcome == "retried")
    ]


@ENABLED
async def test_the_painted_surface_matches_the_middlewares_own_snapshot() -> None:
    args = {"surfaceId": SURFACE_ID, "components": COMPONENTS, "data": DATA}
    body, _seen, _offered = await _run([("call-plain", args)], [_entry(CATALOG)])

    # pydantic-ai's AG-UI event stream stamps every event; the middleware's scenario has none.
    (surface,) = _surfaces(body)
    assert isinstance(surface.pop("timestamp"), int)
    assert surface == GENERATED["snapshots"][0]


_V09_CATALOG = {key: value for key, value in CATALOG.items() if key != "protocolVersion"}
_BOUND_TEXT_V09 = [
    {"id": "root", "component": "Column", "children": ["t"]},
    {"id": "t", "component": "Text", "text": {"path": "/title"}},
]
_BOUND_TEXT_V1 = [
    {"id": "root", "component": "Column", "children": ["t"]},
    {"id": "t", "component": "Text", "text": {"@path": "/title"}},
]


@ENABLED
@pytest.mark.parametrize(
    ("catalog", "bad", "said"),
    [
        (
            CATALOG,
            {"surfaceId": "s", "components": [{"id": "root", "component": "Carousel"}]},
            "Component type 'Carousel' is not in the catalog",
        ),
        (
            CATALOG,
            {"surfaceId": "s", "components": [{"id": "root", "component": "Column"}]},
            "is missing required prop 'children'",
        ),
        (
            CATALOG,
            {"surfaceId": "s", "components": [{"id": "main", "component": "Divider"}]},
            "No component has id 'root'",
        ),
        (
            CATALOG,
            {"surfaceId": "s", "components": _BOUND_TEXT_V09, "data": {"title": "x"}},
            "but the catalog declares protocol 1.0",
        ),
        (
            _V09_CATALOG,
            {"surfaceId": "s", "components": _BOUND_TEXT_V1, "data": {"title": "x"}},
            "but the catalog declares protocol v0.9",
        ),
        (CATALOG, {"surfaceId": "  ", "components": PLAIN_ROOT}, "surfaceId"),
        (CATALOG, {"surfaceId": 7, "components": PLAIN_ROOT}, "surfaceId"),
        (CATALOG, {"components": PLAIN_ROOT}, "surfaceId"),
        (CATALOG, {"surfaceId": "s", "components": PLAIN_ROOT, "data": [1]}, "data"),
    ],
    ids=[
        "unknown-component",
        "missing-prop",
        "no-root",
        "v09-under-1.0",
        "1.0-under-v09",
        "blank-surface",
        "surface-not-text",
        "no-surface",
        "data-not-an-object",
    ],
)
async def test_an_invalid_surface_is_retried_and_never_painted(
    catalog: dict[str, Any], bad: dict[str, Any], said: str
) -> None:
    good = {"surfaceId": "s", "components": PLAIN_ROOT}
    body, seen, _offered = await _run([("call-bad", bad), ("call-good", good)], [_entry(catalog)])

    assert [frame["messageId"] for frame in _surfaces(body)] == ["a2ui-surface-call-good"]
    (retry,) = _retry_texts(seen[-1])
    assert said in retry
    assert "RUN_ERROR" not in body


def _catalog_declaring(version: str | None) -> dict[str, Any]:
    catalog: dict[str, Any] = {"catalogId": "https://example.test/c.json", "components": {}}
    catalog["components"] = {"Column": {}, "Text": {}}
    if version is not None:
        catalog["protocolVersion"] = version
    return catalog


@ENABLED
@pytest.mark.parametrize(
    ("declared", "stamped"),
    [
        ("1.0", "1.0"),
        ("v1.0.0-beta", "1.0"),
        ("V1.0", "1.0"),
        ("0.9", "v0.9"),
        ("2.0", "v0.9"),
        ("one", "v0.9"),
        (None, "v0.9"),
    ],
)
async def test_the_version_is_1_0_only_when_the_catalog_declares_it(
    declared: str | None, stamped: str
) -> None:
    good = {"surfaceId": "s", "components": PLAIN_ROOT}
    body, _seen, _offered = await _run([("call-v", good)], [_entry(_catalog_declaring(declared))])

    (surface,) = _surfaces(body)
    operations = surface["content"]["a2ui_operations"]
    assert [operation["version"] for operation in operations] == [stamped, stamped]
    assert operations[0]["createSurface"] == {
        "surfaceId": "s",
        "catalogId": "https://example.test/c.json",
    }


@ENABLED
async def test_the_tool_is_offered_when_the_catalog_entry_is_posted() -> None:
    _body_text, _seen, offered = await _run([], [_entry(CATALOG)])
    assert "render_a2ui" in offered[0]


@ENABLED
@pytest.mark.parametrize(
    "context",
    [
        [],
        [{"description": "something else", "value": json.dumps(CATALOG)}],
        [{"description": DESCRIPTION, "value": "not json"}],
        [{"description": DESCRIPTION, "value": json.dumps(["a list"])}],
        [{"description": DESCRIPTION, "value": json.dumps({"components": {}})}],
        [{"description": DESCRIPTION, "value": json.dumps({"catalogId": 7, "components": {}})}],
        [{"description": DESCRIPTION, "value": json.dumps({"catalogId": "", "components": {}})}],
        [{"description": DESCRIPTION, "value": json.dumps({"catalogId": "c", "components": []})}],
    ],
    ids=[
        "none",
        "other",
        "unparsable",
        "not-an-object",
        "no-id",
        "id-not-text",
        "empty-id",
        "no-components",
    ],
)
async def test_no_tool_without_a_usable_catalog_entry(context: list[dict[str, str]]) -> None:
    _body_text, _seen, offered = await _run([], context)
    assert "render_a2ui" not in offered[0]


@ENABLED
async def test_a_catalog_nested_past_the_parser_ends_the_run_cleanly_without_the_tool() -> None:
    deep = "[" * 200000 + "]" * 200000
    body, _seen, offered = await _run([], [{"description": DESCRIPTION, "value": deep}])
    assert "RUN_FINISHED" in body
    assert "render_a2ui" not in offered[0]


async def test_no_tool_on_a_mount_that_has_not_opted_in() -> None:
    _body_text, _seen, offered = await _run([], [_entry(CATALOG)])
    assert "render_a2ui" not in offered[0]


def _without_the_toolkit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ag_ui_a2ui_toolkit", None)
    monkeypatch.setitem(sys.modules, "ag_ui_a2ui_toolkit.validate", None)


@ENABLED
def test_an_opted_in_mount_without_the_extra_fails_at_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_the_toolkit(monkeypatch)
    with pytest.raises(ImportError, match=r"install django-ag-ui\[a2ui\]"):
        DjangoAGUIView(_registry(), model=FunctionModel(lambda _m, _i: None))


def test_a_mount_that_has_not_opted_in_never_needs_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_the_toolkit(monkeypatch)
    DjangoAGUIView(_registry(), model=FunctionModel(lambda _m, _i: None))


@ENABLED
async def test_a_1_0_binding_is_not_resolved_against_the_data() -> None:
    # The documented limitation: the toolkit resolves only ``path`` keys, as the middleware does.
    unbound = [
        {"id": "root", "component": "Column", "children": ["t"]},
        {"id": "t", "component": "Text", "text": {"@path": "/missing"}},
    ]
    good = {"surfaceId": "s", "components": unbound, "data": {}}
    body, seen, _offered = await _run([("call-unbound", good)], [_entry(CATALOG)])

    assert [frame["messageId"] for frame in _surfaces(body)] == ["a2ui-surface-call-unbound"]
    assert _retry_texts(seen[-1]) == []


@ENABLED
async def test_a_catalog_with_no_components_is_checked_for_structure_only() -> None:
    catalog = {"catalogId": "https://example.test/c.json", "components": {}}
    calls = [
        ("call-rootless", {"surfaceId": "s", "components": [{"id": "x", "component": "Any"}]}),
        ("call-any", {"surfaceId": "s", "components": [{"id": "root", "component": "Any"}]}),
    ]
    body, seen, _offered = await _run(calls, [_entry(catalog)])

    assert [frame["messageId"] for frame in _surfaces(body)] == ["a2ui-surface-call-any"]
    assert _retry_texts(seen[-1]) == ["components: No component has id 'root'"]


def _capturing() -> tuple[list[Any], FunctionModel]:
    captured: list[Any] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> Any:
        captured.extend(tool for tool in info.function_tools if tool.name == "render_a2ui")
        yield "done"

    return captured, FunctionModel(stream_function=stream)


@ENABLED
@pytest.mark.parametrize(("catalog", "label"), [(CATALOG, "1.0"), (_V09_CATALOG, "v0.9")])
async def test_the_tool_is_worded_as_the_middlewares_render_tool(
    catalog: dict[str, Any], label: str
) -> None:
    captured, model = _capturing()
    await _drain(await DjangoAGUIView(_registry(), model=model)(_post(_body([_entry(catalog)]))))

    # Literals from tools.ts ``renderA2UITool`` in @ag-ui/a2ui-middleware 0.0.16.
    (tool,) = captured
    assert tool.description == (
        f"Render a dynamic A2UI {label} surface with structured parameters. "
        "Follow the A2UI render tool usage guide provided in context."
    )
    assert tool.parameters_json_schema == {
        "type": "object",
        "properties": {
            "surfaceId": {"type": "string", "description": "Unique surface identifier."},
            "components": {
                "type": "array",
                "description": f"A2UI {label} component array (flat format). "
                'The root component must have id "root".',
                "items": {"type": "object"},
            },
            "data": {
                "type": "object",
                "description": "Initial data model for the surface. Written to the root path. "
                'Use for pre-filling form values (e.g. {"form": {"name": "Alice"}}) '
                "or providing data for components bound to data model paths.",
            },
        },
        "required": ["surfaceId", "components"],
    }


@ENABLED
def test_an_opted_in_server_without_the_extra_fails_at_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_the_toolkit(monkeypatch)
    with pytest.raises(ImportError, match=r"install django-ag-ui\[a2ui\]"):
        AGUIServer(_registry(), model=FunctionModel(lambda _m, _i: None))


def test_the_default_run_context_cuts_the_basic_catalog_after_its_components() -> None:
    entry = _entry(CATALOG)
    run_input = SimpleNamespace(context=[SimpleNamespace(**entry)], messages=[])
    block = build_untrusted_context(run_input, config=build_ag_ui_config().run_context)

    assert block is not None
    assert len(entry["value"]) > 20000
    assert "[truncated: client context is capped at 20000 characters]" in block
    assert json.dumps({"components": CATALOG["components"]}, separators=(",", ":"))[1:-1] in block
    assert '"$defs"' not in block
