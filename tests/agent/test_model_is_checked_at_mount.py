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
    """Deliberately not refused."""
    view = DjangoAGUIView(_registry(), model_for_request=lambda _request: TestModel())

    assert view is not None


def test_an_agent_factory_is_left_alone() -> None:
    """It takes full control of construction, model included."""
    view = DjangoAGUIView(_registry(), agent_factory=lambda _registry, _config: None)

    assert view is not None
