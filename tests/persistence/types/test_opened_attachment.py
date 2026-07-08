from __future__ import annotations

from django_ag_ui.persistence.types.attachment_ref import AttachmentRef
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment


def test_pairs_ref_with_bytes() -> None:
    ref = AttachmentRef(id="a1", name="notes.txt", mime="text/plain", size=5)
    opened = OpenedAttachment(ref=ref, content=b"hello")
    assert opened.ref is ref
    assert opened.content == b"hello"
