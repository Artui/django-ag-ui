from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from typing import Any

from django_ag_ui.constants import X_CATEGORY_KEY, X_DESTRUCTIVE_KEY, ToolCategory


def build_input_schema(
    fn: Callable[..., Any],
    *,
    destructive: bool = False,
    category: ToolCategory = ToolCategory.OTHER,
) -> dict[str, Any]:
    """Derive a JSON Schema object from ``fn``'s parameters.

    Supports the primitive types used by the built-in tool surface:
    ``str``, ``int``, ``float``, ``bool``, ``list[T]``, ``dict[str, Any]``,
    and ``X | None`` unions. Anything richer falls back to an empty
    schema fragment (no type constraint), which is still wire-valid JSON
    Schema.

    The ``destructive`` flag is stamped at the schema root as
    ``x-destructive``; ``category`` as ``x-category``. AG-UI passes these
    extensions through verbatim, and frontends read ``x-destructive`` to
    gate execution behind a confirmation modal.
    """
    # `eval_str=True` resolves string annotations (PEP 563 / forward refs)
    # while preserving them verbatim. Unlike `typing.get_type_hints`, it does
    # NOT apply the implicit-`Optional` wrapping that Python <= 3.10 adds to a
    # parameter whose default is `None`, so the derived schema is identical
    # across Python versions (e.g. `anything: Any = None` stays `Any`, not
    # `Any | None`).
    sig = inspect.signature(fn, eval_str=True)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = param.annotation
        hint = Any if annotation is inspect.Parameter.empty else annotation
        properties[name] = _hint_to_schema(hint)
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        X_CATEGORY_KEY: category.value,
    }
    if destructive:
        schema[X_DESTRUCTIVE_KEY] = True
    if required:
        schema["required"] = required
    return schema


def _hint_to_schema(hint: Any) -> dict[str, Any]:
    # PEP 484: a bare ``None`` annotation means ``type(None)``. `eval_str`
    # leaves it as the ``None`` object (unlike `get_type_hints`), so normalise.
    if hint is None:
        hint = type(None)
    if hint is Any:
        return {}
    origin = typing.get_origin(hint)
    if origin is None:
        return _scalar_schema(hint)
    if origin in (list, tuple, set, frozenset):
        args = typing.get_args(hint)
        items = _hint_to_schema(args[0]) if args else {}
        return {"type": "array", "items": items}
    if origin is dict:
        return {"type": "object"}
    if origin is typing.Union or origin is types.UnionType:
        # ``Union[T]`` collapses to ``T`` in Python, so a union here always
        # has >= 2 distinct args. Exactly one non-``None`` arg means the
        # ``X | None`` shape; expose that as a nullable scalar.
        non_none = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(non_none) == 1:
            return {**_hint_to_schema(non_none[0]), "nullable": True}
        return {"anyOf": [_hint_to_schema(a) for a in non_none]}
    return {}


def _scalar_schema(hint: Any) -> dict[str, Any]:
    # Bool is a subclass of int — check bool first.
    if hint is bool:
        return {"type": "boolean"}
    if hint is str:
        return {"type": "string"}
    if hint is int:
        return {"type": "integer"}
    if hint is float:
        return {"type": "number"}
    if hint is type(None):
        return {"type": "null"}
    if hint in (list, tuple, set, frozenset):
        return {"type": "array", "items": {}}
    if hint is dict:
        return {"type": "object"}
    return {}


__all__ = ["build_input_schema"]
