from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django_ag_ui.agent.types.untrusted_context_item import UntrustedContextItem

_LABEL = "Files the user has attached to this conversation"
_READ_HINT = "Use the read_attachment tool with an id to read a file's contents."


def attachment_context_items(messages: Sequence[Any]) -> tuple[UntrustedContextItem, ...]:
    """Derive the attachment manifest from the posted messages themselves.

    The web component augments the user message it posts with an undeclared
    ``attachments`` array of the refs the composer uploaded. ``ag_ui.core``
    validates with ``extra="allow"``, so it arrives intact on ``model_extra``,
    and ``AGUIAdapter.load_messages`` ignores it. Nothing else on the wire
    carries the ids.

    Read from the messages rather than this run's own upload list because the
    client clears its per-run manifest once a run settles, whereas the message
    list is resent whole on every turn — so the refs ride every later turn and,
    once the thread is stored, survive a page reload.

    The parse is deliberately **total**: a non-mapping entry or one with no
    usable ``id`` is skipped in silence and a wrong-typed field falls back, so a
    shape change upstream degrades to "no manifest" rather than an exception
    mid-run. Only ``role == "user"`` messages are read, so a forged assistant
    turn cannot inject a manifest. Nothing parsed here is trusted for
    authorisation: the ids are only useful through ``read_attachment``, which
    resolves them against an owner-scoped store.

    Returns a single item, or none — the model wants one list of the files it
    can read, not one section per message.
    """
    mentions: dict[str, _AttachmentMention] = {}
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        raw = (getattr(message, "model_extra", None) or {}).get("attachments")
        if not isinstance(raw, list):
            continue
        for entry in raw:
            mention = _parse_mention(entry)
            if mention is None or mention.id in mentions:
                continue
            mentions[mention.id] = mention
    if not mentions:
        return ()
    lines = [_line(mention) for mention in mentions.values()]
    return (UntrustedContextItem(label=_LABEL, value="\n".join([*lines, _READ_HINT])),)


@dataclass(frozen=True)
class _AttachmentMention:
    """One attachment ref as it survived a permissive parse.

    Deliberately **not** an ``AttachmentRef``: that record describes a file the
    server issued and every field of it is known good, whereas here every field
    but ``id`` may be missing or wrong-typed. Sharing the server-side type would
    mean widening a trusted record to accommodate untrusted input.
    """

    id: str
    name: str
    mime: str
    size: int | None


def _parse_mention(entry: Any) -> _AttachmentMention | None:
    """One ref, or ``None`` when the entry carries nothing usable."""
    if not isinstance(entry, Mapping):
        return None
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    name = entry.get("name")
    mime = entry.get("mime")
    size = entry.get("size")
    return _AttachmentMention(
        id=identifier,
        name=name if isinstance(name, str) else "",
        mime=mime if isinstance(mime, str) else "",
        size=size if isinstance(size, int) else None,
    )


def _line(mention: _AttachmentMention) -> str:
    """One manifest line, with a stated fallback for every missing field."""
    size = f"{mention.size} bytes" if mention.size is not None else "size unknown"
    return (
        f"- {mention.name or 'attachment'} "
        f"(id: {mention.id}, {mention.mime or 'unknown type'}, {size})"
    )


__all__ = ["attachment_context_items"]
