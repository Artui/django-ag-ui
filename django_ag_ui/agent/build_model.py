from __future__ import annotations

from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider_class

# 0.2.0 accepted a non-standard ``gemini:`` prefix; Pydantic-AI's provider name
# is ``google``. Keep the alias working (back-compat) by normalising it before
# delegating. Everything else is Pydantic-AI's own provider vocabulary.
_PREFIX_ALIASES = {"gemini": "google"}


def build_model(model: str, *, api_key: str | None = None, provider: Any = None) -> Any:
    """Build a Pydantic-AI model from a ``"provider:name"`` string + explicit key.

    Delegates the ``provider:`` prefix → Model-class resolution to Pydantic-AI's
    own :func:`infer_model`, supplying a ``provider_factory`` that injects the
    credentials instead of letting Pydantic-AI read them from the environment:

    - ``provider`` (a ``Provider`` instance, or a dotted path to one) takes
      precedence — used as-is, so it can carry a custom ``base_url`` / client.
    - otherwise ``api_key`` is passed to the prefix's default ``Provider`` class
      (resolved via :func:`infer_provider_class`).

    Because the prefix map lives in Pydantic-AI, every provider it knows works
    automatically (``anthropic``, ``openai``, ``openai-responses``, ``google``,
    ``groq``, ``bedrock``, …) — there is no hand-maintained table to drift out
    of date.

    A bare model name Pydantic-AI can map to a provider (e.g. ``claude-…`` →
    anthropic) is accepted too; only when the provider can't be resolved at all
    is an error raised.

    Raises:
        ImproperlyConfigured: when the model's provider can't be resolved — an
            unknown / uninferable prefix, or the matching provider extra not
            installed. Set ``PROVIDER`` to a ``Provider`` instance or dotted
            path for anything Pydantic-AI can't infer.
    """
    prefix, sep, name = model.partition(":")
    if sep and prefix in _PREFIX_ALIASES:
        model = f"{_PREFIX_ALIASES[prefix]}:{name}"
    try:
        if provider is not None:
            provider_obj = import_string(provider) if isinstance(provider, str) else provider
            return infer_model(model, provider_factory=lambda _name: provider_obj)
        # Cast: ``infer_provider_class`` returns ``type[Provider]`` whose base
        # ``__init__`` is argument-less in stubs; provider subclasses accept
        # ``api_key`` — this is the Pydantic-AI boundary where ``Any`` is allowed.
        return infer_model(
            model,
            provider_factory=lambda name: cast("Any", infer_provider_class(name))(api_key=api_key),
        )
    except (UserError, ValueError, ImportError) as error:
        raise ImproperlyConfigured(
            f"DJANGO_AG_UI: could not build model {model!r} with the supplied "
            f"API_KEY/PROVIDER ({error}). MODEL must be a 'provider:name' string "
            "(e.g. 'anthropic:claude-sonnet-4.6') whose provider Pydantic-AI knows, "
            "with the matching provider extra installed — or set "
            "DJANGO_AG_UI['PROVIDER'] to a Provider instance or dotted path.",
        ) from error


__all__ = ["build_model"]
