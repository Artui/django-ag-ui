"""A view with no model refuses when it is built, not on the first request.

Building the agent is lazy on purpose -- it is reused across runs -- and the
provider package and the API key genuinely cannot be checked early: resolving
them constructs a provider that reads the environment, so a mount-time check
would refuse `manage.py migrate` on a machine with no key.

The *model setting* is different. Reading it touches no credential and no
provider, and it is the error a consumer meets first: a server that builds,
mounts, passes `manage.py check` and then 500s on the first request, naming a
setting they have never heard of.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from pydantic_ai.models.test import TestModel

from django_ag_ui.agent.agui_view import DjangoAGUIView
from tests.agent.test_agui_view import _registry


def test_a_view_with_no_model_anywhere_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_AG_UI"):
        DjangoAGUIView(_registry())


def test_a_configured_model_is_accepted() -> None:
    assert DjangoAGUIView(_registry(), model=TestModel()) is not None


def test_a_per_request_hook_is_left_alone() -> None:
    """Deliberately not refused.

    A hook is at least *intended* to supply a model, and a check that refuses a
    configuration which might work is worse than one that misses a case.
    """
    view = DjangoAGUIView(_registry(), model_for_request=lambda _request: TestModel())

    assert view is not None


def test_an_agent_factory_is_left_alone() -> None:
    """It takes full control of construction, model included."""
    view = DjangoAGUIView(_registry(), agent_factory=lambda _registry, _config: None)

    assert view is not None
