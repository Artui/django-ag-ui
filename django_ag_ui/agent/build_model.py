from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

# Map a ``"provider:name"`` MODEL prefix to the Pydantic-AI Model + Provider
# classes used to build a model with an explicitly-supplied key/provider
# (instead of Pydantic-AI inferring the key from the environment). Dotted paths
# rather than imports, since the provider libs are optional extras.
_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": (
        "pydantic_ai.models.anthropic.AnthropicModel",
        "pydantic_ai.providers.anthropic.AnthropicProvider",
    ),
    "openai": (
        "pydantic_ai.models.openai.OpenAIChatModel",
        "pydantic_ai.providers.openai.OpenAIProvider",
    ),
    "google": (
        "pydantic_ai.models.google.GoogleModel",
        "pydantic_ai.providers.google.GoogleProvider",
    ),
    # Common aliases that select the same Model class as ``google``.
    "google-gla": (
        "pydantic_ai.models.google.GoogleModel",
        "pydantic_ai.providers.google.GoogleProvider",
    ),
    "gemini": (
        "pydantic_ai.models.google.GoogleModel",
        "pydantic_ai.providers.google.GoogleProvider",
    ),
}


def build_model(model: str, *, api_key: str | None = None, provider: Any = None) -> Any:
    """Build a Pydantic-AI model from a ``"provider:name"`` string + explicit key.

    Resolves the ``provider:`` prefix to the matching Model class and constructs
    it with a provider that carries the supplied credentials, instead of relying
    on environment inference:

    - ``provider`` (a ``Provider`` instance, or a dotted path to one) takes
      precedence — used as-is, so it can carry a custom ``base_url`` / client.
    - otherwise ``api_key`` is passed to the prefix's default ``Provider``.

    Raises:
        ImproperlyConfigured: when ``model`` has no ``provider:`` prefix, or the
            prefix is not a known provider (the caller should set ``PROVIDER``).
    """
    prefix, sep, name = model.partition(":")
    if not sep:
        raise ImproperlyConfigured(
            "DJANGO_AG_UI['MODEL'] must be a 'provider:name' string (e.g. "
            f"'anthropic:claude-sonnet-4.6') when API_KEY/PROVIDER is set; got {model!r}.",
        )
    entry = _PROVIDERS.get(prefix)
    if entry is None:
        raise ImproperlyConfigured(
            f"DJANGO_AG_UI: model prefix {prefix!r} is not a known provider for "
            "API_KEY-based construction; set DJANGO_AG_UI['PROVIDER'] to a Provider "
            "instance or dotted path instead.",
        )
    model_path, provider_path = entry
    model_cls = import_string(model_path)
    if provider is not None:
        provider_obj = import_string(provider) if isinstance(provider, str) else provider
    else:
        provider_obj = import_string(provider_path)(api_key=api_key)
    return model_cls(name, provider=provider_obj)


__all__ = ["build_model"]
