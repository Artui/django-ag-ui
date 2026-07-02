from __future__ import annotations

from typing import Any, cast

from django.http import HttpRequest
from pydantic_ai.toolsets.function import FunctionToolset

from django_ag_ui.persistence.types.attachment_store import AttachmentStore
from django_ag_ui.persistence.types.opened_attachment import OpenedAttachment

# Non-``text/*`` content types whose bytes are still UTF-8 text worth inlining.
_TEXTUAL_MIMES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "image/svg+xml",
    }
)


def build_attachment_toolset(store: AttachmentStore, request: HttpRequest) -> FunctionToolset[None]:
    """Build a per-request toolset exposing ``read_attachment`` over ``store``.

    Mirrors the per-request ``drf-mcp`` bridge: the request is captured in a
    closure so ``store.open`` is owner-scoped to the acting AG-UI user — the
    model can only read files that user uploaded, never another's by id. Wired
    in by :class:`~django_ag_ui.DjangoAGUIView` when
    ``DJANGO_AG_UI["ATTACHMENT_STORE"]`` is set, so the wire stays vanilla
    AG-UI: attachments travel as lightweight refs and the bytes are reached here,
    server-side, only when the model asks.
    """

    async def read_attachment(attachment_id: str) -> str:
        """Read a file the user attached to this conversation.

        Pass the ``id`` from an attachment chip on the user's message. Returns
        the file's text when it is textual; for binary files (images, PDFs, …)
        returns a short note with the name, type, and size rather than raw bytes.
        """
        opened = await store.open(attachment_id, request=request)
        if opened is None:
            return f"No attachment with id {attachment_id!r} is available."
        return _render(opened)

    # ``read_attachment`` is a plain (no-``RunContext``) tool; pydantic-ai's
    # FunctionToolset overloads only type the ctx-taking form, so cast at the
    # boundary rather than reshape the function around the type checker.
    return FunctionToolset([cast("Any", read_attachment)], id="django-ag-ui-attachments")


def _render(opened: OpenedAttachment) -> str:
    """Decoded text for a textual attachment, else a one-line binary manifest."""
    ref = opened.ref
    # The tool needs the whole content to decode / classify; read it under a
    # ``with`` so the streaming handle is always closed.
    with opened.content as handle:
        data = handle.read()
    text = _as_text(data, ref.mime)
    if text is not None:
        return text
    return (
        f"[{ref.name}] is a {ref.mime or 'binary'} file ({ref.size} bytes); "
        "its content is not text and was not inlined."
    )


def _as_text(content: bytes, mime: str) -> str | None:
    """UTF-8 text for a textual content type, or ``None`` when binary/undecodable."""
    if not (mime.startswith("text/") or mime in _TEXTUAL_MIMES):
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


__all__ = ["build_attachment_toolset"]
