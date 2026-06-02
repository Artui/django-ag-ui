from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Union

from django_ag_ui.constants import ToolCategory
from django_ag_ui.registry.build_input_schema import build_input_schema


class _Custom:
    """Module-level marker for the 'unrecognised class falls through' branch."""


def test_primitive_parameters() -> None:
    def fn(
        s: str,
        n: int,
        b: bool,
        f: float,
        none: None = None,
        anything: Any = None,
    ) -> None:
        del s, n, b, f, none, anything

    schema = build_input_schema(fn)
    props = schema["properties"]
    assert props["s"] == {"type": "string"}
    assert props["n"] == {"type": "integer"}
    assert props["b"] == {"type": "boolean"}
    assert props["f"] == {"type": "number"}
    assert props["none"] == {"type": "null"}
    assert props["anything"] == {}
    assert schema["required"] == ["s", "n", "b", "f"]
    assert schema["additionalProperties"] is False
    # Category default is stamped; no x-destructive when not destructive.
    assert schema["x-category"] == "other"
    assert "x-destructive" not in schema


def test_destructive_and_category_extensions() -> None:
    def fn(value: str) -> None:
        del value

    schema = build_input_schema(fn, destructive=True, category=ToolCategory.UI_WRITE)
    assert schema["x-destructive"] is True
    assert schema["x-category"] == "ui_write"
    # No x-confirm unless a confirm prompt is supplied.
    assert "x-confirm" not in schema


def test_confirm_extension() -> None:
    def fn(value: str) -> None:
        del value

    schema = build_input_schema(fn, destructive=True, confirm="Activate this project?")
    assert schema["x-confirm"] == "Activate this project?"
    assert "x-summary" not in schema


def test_summary_extension() -> None:
    def fn(value: str) -> None:
        del value

    schema = build_input_schema(fn, summary="Query orders")
    assert schema["x-summary"] == "Query orders"


def test_container_types() -> None:
    def fn(
        names: list[str],
        pairs: dict[str, int],
        seen: set[int],
        coords: tuple[int, ...],
    ) -> None:
        del names, pairs, seen, coords

    props = build_input_schema(fn)["properties"]
    assert props["names"] == {"type": "array", "items": {"type": "string"}}
    assert props["pairs"] == {"type": "object"}
    assert props["seen"] == {"type": "array", "items": {"type": "integer"}}
    assert props["coords"]["type"] == "array"


def test_union_types() -> None:
    def fn(
        a: int | None,
        b: str | int,
        c: Union[str, int, None],  # noqa: UP007 — intentional legacy form
    ) -> None:
        del a, b, c

    props = build_input_schema(fn)["properties"]
    assert props["a"] == {"type": "integer", "nullable": True}
    assert "anyOf" in props["b"]
    assert "anyOf" in props["c"]


def test_var_args_are_skipped() -> None:
    def fn(*args: int, **kwargs: str) -> None:
        del args, kwargs

    schema = build_input_schema(fn)
    assert schema["properties"] == {}
    assert "required" not in schema


def test_array_without_args_falls_back() -> None:
    def fn(items: list) -> None:  # noqa: ANN001 — intentional bare ``list``
        del items

    props = build_input_schema(fn)["properties"]
    assert props["items"] == {"type": "array", "items": {}}


def test_bare_dict_and_unknown_class() -> None:
    def fn(d: dict, custom: _Custom) -> None:  # noqa: ANN001
        del d, custom

    props = build_input_schema(fn)["properties"]
    assert props["d"] == {"type": "object"}
    assert props["custom"] == {}


def test_unknown_origin_yields_empty_fragment() -> None:
    def fn(x: Mapping[str, int]) -> None:
        del x

    props = build_input_schema(fn)["properties"]
    assert props["x"] == {}


def test_no_defaults_omits_required() -> None:
    def fn(a: int = 1) -> None:
        del a

    assert "required" not in build_input_schema(fn)
