from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from typing import Any

from django_ag_ui.registry.build_input_schema import build_input_schema
from django_ag_ui.registry.types.tool_binding import ToolBinding
from django_ag_ui.registry.types.tool_spec import ToolSpec


class ToolRegistry:
    """An ordered, named collection of server-side tools.

    State lives on the instance: a ``DjangoAGUIView`` holds one registry;
    tests build a fresh registry per scenario. The registry derives a
    JSON Schema for each tool at registration (including the
    ``x-destructive`` / ``x-category`` extensions) and can dispatch sync
    or async callables.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, ToolBinding] = {}

    def register(self, spec: ToolSpec) -> ToolBinding:
        """Register ``spec`` and return its binding.

        Raises:
            ValueError: when ``spec.name`` is already registered.
        """
        if spec.name in self._bindings:
            raise ValueError(f"tool {spec.name!r} already registered")
        binding = ToolBinding(
            spec=spec,
            input_schema=build_input_schema(
                spec.fn,
                destructive=spec.destructive,
                category=spec.category,
                confirm=spec.confirm,
                summary=spec.summary,
            ),
        )
        self._bindings[spec.name] = binding
        return binding

    def __iter__(self) -> Iterator[ToolBinding]:
        return iter(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings

    def get(self, name: str) -> ToolBinding:
        """Return the binding for ``name`` or raise ``KeyError``."""
        try:
            return self._bindings[name]
        except KeyError as e:
            raise KeyError(f"tool {name!r} is not registered") from e

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Dispatch a sync call to the registered tool.

        Refuses coroutine functions to avoid silently returning an
        un-awaited coroutine. Use :meth:`acall` for async tools.
        """
        fn = self.get(name).spec.fn
        if inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"tool {name!r} is async; use ToolRegistry.acall instead",
            )
        return _invoke(fn, arguments)

    async def acall(self, name: str, arguments: dict[str, Any]) -> Any:
        """Dispatch an async call; transparently awaits sync callables."""
        fn = self.get(name).spec.fn
        if inspect.iscoroutinefunction(fn):
            return await _ainvoke(fn, arguments)
        return _invoke(fn, arguments)


def _invoke(fn: Callable[..., Any], arguments: dict[str, Any]) -> Any:
    sig = inspect.signature(fn)
    bound = sig.bind(**arguments)
    bound.apply_defaults()
    return fn(*bound.args, **bound.kwargs)


async def _ainvoke(fn: Callable[..., Any], arguments: dict[str, Any]) -> Any:
    sig = inspect.signature(fn)
    bound = sig.bind(**arguments)
    bound.apply_defaults()
    return await fn(*bound.args, **bound.kwargs)


__all__ = ["ToolRegistry"]
