from __future__ import annotations

import pytest

from django_ag_ui.persistence.django_session_conversation_store import (
    DjangoSessionConversationStore,
)
from django_ag_ui.persistence.null_conversation_store import NullConversationStore
from django_ag_ui.persistence.resolve_conversation_store import resolve_conversation_store

_SESSION_STORE = (
    "django_ag_ui.persistence.django_session_conversation_store.DjangoSessionConversationStore"
)


def test_none_returns_null_store() -> None:
    assert isinstance(resolve_conversation_store(None), NullConversationStore)


def test_valid_dotted_path_loads_store() -> None:
    assert isinstance(resolve_conversation_store(_SESSION_STORE), DjangoSessionConversationStore)


def test_invalid_path_raises() -> None:
    with pytest.raises(ValueError, match="invalid conversation store path"):
        resolve_conversation_store("NotADottedPath")


def test_non_store_class_rejected() -> None:
    with pytest.raises(TypeError, match="ConversationStore"):
        resolve_conversation_store("collections.OrderedDict")
