from __future__ import annotations

import pytest

from django_ag_ui.constants import ToolCategory
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.registry.types.tool_spec import ToolSpec


def make_spec(name: str = "noop") -> ToolSpec:
    def fn(x: int = 1) -> int:
        return x + 1

    return ToolSpec(name=name, fn=fn, description="d")


def test_register_and_dispatch() -> None:
    reg = ToolRegistry()
    binding = reg.register(make_spec())
    assert "noop" in reg
    assert len(reg) == 1
    assert list(reg) == [binding]
    assert reg.get("noop") is binding
    assert binding.input_schema["x-category"] == ToolCategory.OTHER.value
    assert reg.call("noop", {"x": 5}) == 6
    assert reg.call("noop", {}) == 2  # default applies


def test_register_duplicate_name_rejected() -> None:
    reg = ToolRegistry()
    reg.register(make_spec())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(make_spec())


def test_get_missing_raises_keyerror() -> None:
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="not registered"):
        reg.get("ghost")


def test_call_unknown_raises_keyerror() -> None:
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.call("ghost", {})


def test_call_async_function_refused_from_sync() -> None:
    async def coro(x: int = 1) -> int:
        return x

    reg = ToolRegistry()
    reg.register(ToolSpec(name="coro", fn=coro, description="d"))
    with pytest.raises(TypeError, match="async"):
        reg.call("coro", {})


async def test_acall_dispatches_sync_and_async() -> None:
    async def coro(x: int) -> int:
        return x * 2

    def sync(x: int) -> int:
        return x + 1

    reg = ToolRegistry()
    reg.register(ToolSpec(name="coro", fn=coro, description="d"))
    reg.register(ToolSpec(name="sync", fn=sync, description="d"))

    assert await reg.acall("coro", {"x": 3}) == 6
    assert await reg.acall("sync", {"x": 3}) == 4
