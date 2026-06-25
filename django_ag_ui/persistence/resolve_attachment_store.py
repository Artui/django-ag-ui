from __future__ import annotations

from importlib import import_module

from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.types.attachment_store import AttachmentStore


def resolve_attachment_store(dotted_path: str | None) -> AttachmentStore:
    """Instantiate the attachment store referenced by a dotted path.

    ``None`` yields a :class:`NullAttachmentStore` (uploads off). The path must
    point to a class importable with no arguments; a store needing constructor
    arguments should be wired by the consumer. Mirrors
    :func:`~django_ag_ui.resolve_conversation_store`.
    """
    if dotted_path is None:
        return NullAttachmentStore()
    module_path, _, attr = dotted_path.rpartition(".")
    if not module_path:
        raise ValueError(f"invalid attachment store path: {dotted_path!r}")
    cls = getattr(import_module(module_path), attr)
    instance = cls()
    if not isinstance(instance, AttachmentStore):
        raise TypeError(
            f"{dotted_path} did not produce an AttachmentStore; got {type(instance).__name__}",
        )
    return instance


__all__ = ["resolve_attachment_store"]
