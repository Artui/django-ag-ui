from __future__ import annotations

import pytest

from django_ag_ui.persistence.null_attachment_store import NullAttachmentStore
from django_ag_ui.persistence.resolve_attachment_store import resolve_attachment_store


def test_none_yields_null_store() -> None:
    assert isinstance(resolve_attachment_store(None), NullAttachmentStore)


def test_resolves_a_dotted_path() -> None:
    store = resolve_attachment_store(
        "django_ag_ui.persistence.null_attachment_store.NullAttachmentStore"
    )
    assert isinstance(store, NullAttachmentStore)


def test_bare_name_without_module_raises() -> None:
    with pytest.raises(ValueError, match="invalid attachment store path"):
        resolve_attachment_store("NullAttachmentStore")


def test_non_store_class_raises() -> None:
    with pytest.raises(TypeError, match="did not produce an AttachmentStore"):
        resolve_attachment_store("django.http.HttpRequest")
