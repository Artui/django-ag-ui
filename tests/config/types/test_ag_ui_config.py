"""The resolved config record — what it holds, and what it must not print."""

from __future__ import annotations

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config


def test_the_provider_key_is_absent_from_the_repr() -> None:
    """A secret nested in another object's repr survives name-based scrubbing.

    This record is bound to a plainly-named local on every path that builds an
    agent, so a bad model string, a provider import error or a provider 401 puts
    the whole config in the frame locals of a technical-500 page or an
    error-reporting event. A scrubber looking for a field called ``api_key``
    finds ``config`` there instead and passes the key through verbatim.
    """
    config = build_ag_ui_config(api_key="sk-not-in-a-traceback")

    rendered = repr(config)
    assert "sk-not-in-a-traceback" not in rendered
    # The field is suppressed, not dropped: the value is still what the provider
    # is built with.
    assert config.api_key == "sk-not-in-a-traceback"


def test_the_rest_of_the_record_still_renders() -> None:
    # Suppressing one field must not cost the debuggability the repr is for.
    rendered = repr(build_ag_ui_config(api_key="sk-secret", retries=3))
    assert "retries=3" in rendered
    assert "api_key" not in rendered
