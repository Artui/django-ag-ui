from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from django_ag_ui.agent.build_model import build_model

# A module-level provider instance referenced by dotted path in one test.
DOTTED_PROVIDER = AnthropicProvider(api_key="from-dotted-path")


def test_builds_anthropic_from_api_key() -> None:
    model = build_model("anthropic:claude-sonnet-4-5", api_key="sk-test")
    assert isinstance(model, AnthropicModel)


def test_builds_openai_from_api_key() -> None:
    # Pydantic-AI 2.x maps the bare ``openai:`` prefix to the Responses API model;
    # ``openai-chat:`` still selects the Chat Completions model.
    assert isinstance(build_model("openai:gpt-4o", api_key="sk-test"), OpenAIResponsesModel)
    assert isinstance(build_model("openai-chat:gpt-4o", api_key="sk-test"), OpenAIChatModel)


def test_builds_openai_responses_from_api_key() -> None:
    # The `openai-responses:` prefix (and any other Pydantic-AI knows) now works
    # on the API_KEY path without a hand-maintained provider table.
    model = build_model("openai-responses:gpt-5", api_key="sk-test")
    assert isinstance(model, OpenAIResponsesModel)


def test_builds_google_from_api_key() -> None:
    assert isinstance(build_model("google:gemini-2.0-flash", api_key="k"), GoogleModel)


def test_google_aliases_select_the_google_model() -> None:
    # ``gemini:`` is our own back-compat alias for Pydantic-AI's ``google:``
    # provider (its only Google provider name in 2.x — the 1.x ``google-gla:`` /
    # ``google-vertex:`` prefixes were dropped upstream).
    assert isinstance(build_model("gemini:gemini-2.0-flash", api_key="k"), GoogleModel)


def test_provider_instance_takes_precedence() -> None:
    provider = AnthropicProvider(api_key="from-instance")
    model = build_model("anthropic:claude-sonnet-4-5", provider=provider)
    assert isinstance(model, AnthropicModel)


def test_provider_dotted_path_is_resolved() -> None:
    model = build_model(
        "anthropic:claude-sonnet-4-5",
        provider="tests.agent.test_build_model.DOTTED_PROVIDER",
    )
    assert isinstance(model, AnthropicModel)


def test_bare_model_name_no_longer_infers_a_provider() -> None:
    # Pydantic-AI 2.x dropped bare-name provider inference: a recognisable name
    # like ``claude-…`` without a ``provider:`` prefix no longer resolves and now
    # points the user at the PROVIDER setting, same as any uninferable name.
    with pytest.raises(ImproperlyConfigured, match="PROVIDER"):
        build_model("claude-sonnet-4-5", api_key="k")


def test_uninferable_bare_model_points_at_provider_setting() -> None:
    with pytest.raises(ImproperlyConfigured, match="PROVIDER"):
        build_model("no-such-model", api_key="k")


def test_unknown_prefix_points_at_provider_setting() -> None:
    with pytest.raises(ImproperlyConfigured, match="PROVIDER"):
        build_model("bogus:model", api_key="k")
