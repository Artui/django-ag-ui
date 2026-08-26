"""Every directly-mountable endpoint asks the same authentication question."""

from __future__ import annotations

import warnings
from types import SimpleNamespace
from typing import Any

import pytest

from django_ag_ui.persistence.attachments_view import AttachmentsView
from django_ag_ui.persistence.threads_view import ThreadsView
from django_ag_ui.persistence.transcribe_view import TranscribeView

BUILDERS = {
    "attachments": lambda **kw: AttachmentsView(store=None, **kw),
    "threads": lambda **kw: ThreadsView(store=None, **kw),
    "transcribe": lambda **kw: TranscribeView(backend=None, **kw),
}


@pytest.mark.parametrize("build", BUILDERS.values(), ids=BUILDERS)
def test_an_endpoint_that_never_states_how_it_authenticates_warns(build: Any) -> None:
    """The warning used to fire on the agent endpoint alone.

    A project mounting these directly -- which the docs describe -- got silence,
    so the one configuration the warning exists for (cookie-authenticated callers
    on a CSRF-exempt endpoint, where any third-party page can drive it) was
    announced on one mount and not on its siblings.
    """
    with pytest.warns(RuntimeWarning, match="CSRF-exempt"):
        build()


@pytest.mark.parametrize("build", BUILDERS.values(), ids=BUILDERS)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"csrf_exempt": False},
        {"csrf_exempt": True},
        {"get_user": lambda _request: SimpleNamespace(is_authenticated=True)},
    ],
    ids=["csrf-enforced", "csrf-exempt-deliberately", "get-user-hook"],
)
def test_a_stated_configuration_is_silent(build: Any, kwargs: dict[str, Any]) -> None:
    """Unstated is the signal, not ``True``.

    Warning on a correct configuration is how a warning gets filtered, and then
    stops being read at all.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        build(**kwargs)
