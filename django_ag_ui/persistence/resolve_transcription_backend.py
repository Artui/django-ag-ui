from __future__ import annotations

from importlib import import_module

from django_ag_ui.persistence.null_transcription_backend import NullTranscriptionBackend
from django_ag_ui.persistence.types.transcription_backend import TranscriptionBackend


def resolve_transcription_backend(dotted_path: str | None) -> TranscriptionBackend:
    """Instantiate the transcription backend referenced by a dotted path.

    ``None`` yields a :class:`NullTranscriptionBackend` (voice off). The path
    must point to a class importable with no arguments; a backend needing
    constructor arguments should be wired by the consumer (subclass it and set
    class attributes). Mirrors
    :func:`~django_ag_ui.resolve_attachment_store`.
    """
    if dotted_path is None:
        return NullTranscriptionBackend()
    module_path, _, attr = dotted_path.rpartition(".")
    if not module_path:
        raise ValueError(f"invalid transcription backend path: {dotted_path!r}")
    cls = getattr(import_module(module_path), attr)
    instance = cls()
    if not isinstance(instance, TranscriptionBackend):
        raise TypeError(
            f"{dotted_path} did not produce a TranscriptionBackend; got {type(instance).__name__}",
        )
    return instance


__all__ = ["resolve_transcription_backend"]
