from __future__ import annotations

import pytest

from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.resolve_transcription_backend import resolve_transcription_backend


def test_none_yields_null_backend() -> None:
    assert isinstance(resolve_transcription_backend(None), NullTranscriptionBackend)


def test_resolves_a_dotted_path() -> None:
    backend = resolve_transcription_backend(
        "django_ag_ui.persistence.null_transcription_backend.NullTranscriptionBackend"
    )
    assert isinstance(backend, NullTranscriptionBackend)


def test_bare_name_without_module_raises() -> None:
    with pytest.raises(ValueError, match="invalid transcription backend path"):
        resolve_transcription_backend("NullTranscriptionBackend")


def test_non_backend_class_raises() -> None:
    with pytest.raises(TypeError, match="did not produce a TranscriptionBackend"):
        resolve_transcription_backend("django.http.HttpRequest")
