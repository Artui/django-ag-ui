from __future__ import annotations

from importlib import import_module

from django_ag_ui.persistence.null_conversation_store import NullConversationStore
from django_ag_ui.persistence.types.conversation_store import ConversationStore


def resolve_conversation_store(dotted_path: str | None) -> ConversationStore:
    """Instantiate the conversation store referenced by a dotted path.

    ``None`` yields a ``NullConversationStore`` (persistence off). The path must
    point to a class importable with no arguments; a store needing constructor
    arguments should be wired by the consumer.
    """
    if dotted_path is None:
        return NullConversationStore()
    module_path, _, attr = dotted_path.rpartition(".")
    if not module_path:
        raise ValueError(f"invalid conversation store path: {dotted_path!r}")
    cls = getattr(import_module(module_path), attr)
    instance = cls()
    if not isinstance(instance, ConversationStore):
        raise TypeError(
            f"{dotted_path} did not produce a ConversationStore; got {type(instance).__name__}",
        )
    return instance


__all__ = ["resolve_conversation_store"]
