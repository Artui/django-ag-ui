"""``DJANGO_AG_UI["MODEL"] = "test"`` stands the whole mount up with no credential.

Standing this stack up produces its configuration errors one HTTP request at a
time, and the last one is a provider credential -- so the final step of a
deployment is the one step a consumer cannot rehearse before a key exists. The
string ``"test"`` is what closes that gap: Pydantic-AI's ``infer_model``
short-circuits it to a ``TestModel`` before any provider is resolved, and
``_resolve_model_value`` hands a model string through untouched, so the settings
read, the mount, the gate and the SSE transport all run for real against a model
that talks to nothing.

The suite is otherwise no evidence for this. Every other fixture here passes
``model=TestModel()`` to the constructor, which answers a different question --
how to inject a double into a *test* -- and skips the settings read the recipe is
made of. These tests drive the recipe as written: nothing on the constructor, the
value in ``DJANGO_AG_UI``, and a real ``path()`` mount underneath.

What is deliberately *not* re-tested here: that a missing ``MODEL`` still refuses
at construction (``test_model_is_checked_at_mount``) and that an anonymous caller
still gets a 401 (``test_agui_view.test_anonymous_is_rejected_by_default``).
Neither changes under this recipe, and a copy of each would only add a second
place to update.
"""

from __future__ import annotations

from typing import Any

from django.test import AsyncClient, override_settings
from django_pydantic_agent.registry.decorator import tool
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.agent.test_agui_view import _post, _run_input

_REHEARSAL = {"MODEL": "test"}


async def _stream(response: Any) -> str:
    chunks = [c.decode() if isinstance(c, bytes) else c async for c in response.streaming_content]
    return "".join(chunks)


@override_settings(DJANGO_AG_UI=_REHEARSAL, ROOT_URLCONF="tests.agent.rehearsal_urls")
async def test_the_settings_model_alone_streams_a_complete_run() -> None:
    """The claim the docs make, driven the way the docs write it.

    Routing, the auth gate's ``get_user`` hook, the ``RunAgentInput`` parse, the
    agent build, a server-side tool call and the SSE encoding all happen; the one
    thing that does not is a provider. A run that reaches ``RUN_FINISHED`` with no
    ``RUN_ERROR`` is therefore evidence about the consumer's *wiring*, which is
    exactly the half a key would otherwise gate.
    """
    response = await AsyncClient().post(
        "/agent/", data=_run_input("double 5"), content_type="application/json"
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/event-stream"
    body = await _stream(response)
    assert "RUN_STARTED" in body
    assert "RUN_FINISHED" in body
    assert "RUN_ERROR" not in body
    # The registered tool really ran -- so the rehearsal covers the registry, not
    # only the transport around it.
    assert "double" in body


@override_settings(DJANGO_AG_UI=_REHEARSAL)
def test_the_bare_string_reaches_pydantic_ai_untouched() -> None:
    """The mechanism, separately from the run that depends on it.

    ``_resolve_model_value`` only rewrites a model string when a key or provider
    was configured; with neither, ``"test"`` is what Pydantic-AI receives, and
    ``infer_model`` maps that one string to a ``TestModel``. Asserting the
    resolved value pins which of the two paths the recipe takes -- the streaming
    test alone would still pass if the string were being rebuilt through a
    provider that happened to work.
    """
    assert DjangoAGUIView(ToolRegistry(), csrf_exempt=True)._resolve_model() == "test"


@override_settings(DJANGO_AG_UI={"MODEL": "test", "API_KEY": "not-a-real-key"})
def test_a_leftover_api_key_does_not_disturb_the_rehearsal() -> None:
    """Documented, because the obvious reading of the code says otherwise.

    With a key configured the string is routed through ``build_model``, which
    looks like it would construct a provider around ``"test"`` and fail. It does
    not: ``infer_model`` answers the ``"test"`` string before it parses a provider
    prefix, so the ``provider_factory`` is never called. A consumer rehearsing on
    a machine that already carries a key therefore changes one setting, not two --
    worth pinning, because the day that stops being true the docs are telling
    people to leave a setting in place that breaks the recipe.
    """
    resolved = DjangoAGUIView(ToolRegistry(), csrf_exempt=True)._resolve_model()

    assert isinstance(resolved, TestModel)


async def test_the_rehearsal_calls_every_registered_tool_unprompted() -> None:
    """The caveat the docs carry, held to by a test so it cannot quietly lapse.

    ``TestModel`` defaults to ``call_tools="all"`` and synthesises arguments, so
    it exercises a destructive tool as readily as a read-only one and pays no
    attention to what the user asked for -- here, a message that requests
    nothing. That makes the rehearsal a poor thing to point at a real database,
    and it is not a fact a reader can infer from "it stands the stack up".
    """
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
