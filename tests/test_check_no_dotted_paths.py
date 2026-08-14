"""A collaborator named rather than passed, refused where it is cheap to notice.

The settings-shaped version of this mistake has been refused since 0.19.0
(`test_check_removed_settings`). The constructor-shaped version was not, and it
was the worse of the two: a string reached the endpoint the argument configures
and failed there, one request later, as an attribute error on a `str`.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from django_ag_ui import AGUIServer, ToolRegistry


@pytest.mark.parametrize(
    "argument",
    [
        "agent_factory",
        "attachment_store",
        "audit_logger",
        "authorize",
        "conversation_store",
        "deps_factory",
        "drf_mcp_server",
        "get_user",
        "instructions_for_request",
        "model_for_request",
        "provider",
        "service_specs",
        "skills",
        "step_store",
        "throttle",
        "transcription_backend",
    ],
)
def test_a_collaborator_passed_as_a_dotted_path_is_refused(argument: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="never dotted paths"):
        AGUIServer(ToolRegistry(), model=TestModel(), **{argument: "myapp.stores.MyStore"})


def test_the_error_names_the_argument_and_the_value() -> None:
    """Both, because the message has to be actionable from a deploy log alone."""
    with pytest.raises(ImproperlyConfigured) as excinfo:
        AGUIServer(ToolRegistry(), model=TestModel(), attachment_store="myapp.stores.MyStore")

    message = str(excinfo.value)
    assert "attachment_store=" in message
    assert "myapp.stores.MyStore" in message


def test_every_offender_is_named_at_once() -> None:
    """A project converting an old settings dict has several of these."""
    with pytest.raises(ImproperlyConfigured) as excinfo:
        AGUIServer(
            ToolRegistry(),
            model=TestModel(),
            attachment_store="a.Store",
            conversation_store="b.Store",
            transcription_backend="c.Backend",
        )

    message = str(excinfo.value)
    assert "attachment_store" in message
    assert "conversation_store" in message
    assert "transcription_backend" in message


def test_a_string_model_is_left_alone() -> None:
    """``model`` and ``instructions`` take strings by design.

    The guard is about arguments that can only be objects; refusing every string
    would refuse the documented way to name a model.
    """
    server = AGUIServer(
        ToolRegistry(), model="anthropic:claude-sonnet-4.6", instructions="Be terse"
    )

    assert server.urls is not None


@pytest.mark.parametrize("argument", ["toolsets", "capabilities"])
def test_a_dotted_path_inside_a_list_is_refused_too(argument: str) -> None:
    """The same mistake with a longer fuse, so it is worth the element check.

    A string toolset survives construction *and* the mount, then fails when the
    agent is built — as a missing attribute on something that was never a toolset,
    at which point nothing points back at the URL conf.
    """
    with pytest.raises(ImproperlyConfigured, match="never dotted paths"):
        AGUIServer(ToolRegistry(), model=TestModel(), **{argument: ["myapp.tools.Toolset"]})


def test_a_list_of_real_collaborators_passes() -> None:
    """The element check must not refuse the shape that is actually correct."""
    server = AGUIServer(ToolRegistry(), model=TestModel(), toolsets=[FunctionToolset()])

    assert server.urls is not None
