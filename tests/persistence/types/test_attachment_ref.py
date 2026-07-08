from __future__ import annotations

import dataclasses

import pytest

from django_ag_ui.persistence.types.attachment_ref import AttachmentRef


def test_url_defaults_to_none() -> None:
    ref = AttachmentRef(id="a1", name="notes.txt", mime="text/plain", size=12)
    assert (ref.id, ref.name, ref.mime, ref.size, ref.url) == (
        "a1",
        "notes.txt",
        "text/plain",
        12,
        None,
    )


def test_is_frozen() -> None:
    ref = AttachmentRef(id="a1", name="notes.txt", mime="text/plain", size=12, url="/d/a1/")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.name = "renamed.txt"  # type: ignore[misc]
