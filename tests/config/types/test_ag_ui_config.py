from __future__ import annotations

from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config


def test_the_provider_key_is_absent_from_the_repr() -> None:
    """A secret nested in another object's repr survives name-based scrubbing."""
    config = build_ag_ui_config(api_key="sk-not-in-a-traceback")

    rendered = repr(config)
    assert "sk-not-in-a-traceback" not in rendered
    assert config.api_key == "sk-not-in-a-traceback"


def test_the_rest_of_the_record_still_renders() -> None:
    # Suppressing one field must not cost the debuggability the repr is for.
    rendered = repr(build_ag_ui_config(api_key="sk-secret", retries=3))
    assert "retries=3" in rendered
    assert "api_key" not in rendered
