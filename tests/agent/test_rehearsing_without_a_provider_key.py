from __future__ import annotations

from typing import Any

from django.test import AsyncClient, override_settings
from django_spec_agents.registry.decorator import tool
from django_spec_agents.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.agent.test_agui_view import _post, _run_input

_REHEARSAL = {"MODEL": "test"}


async def _stream(response: Any) -> str:
    chunks = [c.decode() if isinstance(c, bytes) else c async for c in response.streaming_content]
    return "".join(chunks)


@override_settings(DJANGO_AG_UI=_REHEARSAL, ROOT_URLCONF="tests.agent.rehearsal_urls")
async def test_the_settings_model_alone_streams_a_complete_run() -> None:
    """The claim the docs make, driven the way the docs write it."""
    response = await AsyncClient().post(
        "/agent/", data=_run_input("double 5"), content_type="application/json"
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/event-stream"
    body = await _stream(response)
    assert "RUN_STARTED" in body
    assert "RUN_FINISHED" in body
    assert "RUN_ERROR" not in body
    assert "double" in body


@override_settings(DJANGO_AG_UI=_REHEARSAL)
def test_the_bare_string_reaches_pydantic_ai_untouched() -> None:
    """The mechanism, separately from the run that depends on it."""
    assert DjangoAGUIView(ToolRegistry(), csrf_exempt=True)._resolve_model() == "test"


@override_settings(DJANGO_AG_UI={"MODEL": "test", "API_KEY": "not-a-real-key"})
def test_a_leftover_api_key_does_not_disturb_the_rehearsal() -> None:
    """Documented, because the obvious reading of the code says otherwise."""
    resolved = DjangoAGUIView(ToolRegistry(), csrf_exempt=True)._resolve_model()

    assert isinstance(resolved, TestModel)


async def test_the_rehearsal_calls_every_registered_tool_unprompted() -> None:
    """The caveat the docs carry, held to by a test so it cannot quietly lapse."""
    called: list[str] = []
    registry = ToolRegistry()

    @tool(registry, destructive=True, confirm="Really delete?")
    def delete_room(name: str) -> str:
        """Delete a room."""
        called.append(name)
        return "gone"

    with override_settings(DJANGO_AG_UI=_REHEARSAL):
        view = DjangoAGUIView(registry, csrf_exempt=True)
    await _stream(await view(_post(_run_input("just say hello"))))

    assert called, "TestModel ran no tool -- the caveat may no longer hold"
